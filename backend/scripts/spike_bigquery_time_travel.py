"""E12-T2 spike: is the BigQuery time-travel window observable, and can erasure-verify probe it?

Run by hand, never in the PR gate, as a module from backend/ (not `python scripts/...`):

    cd backend && python -m scripts.spike_bigquery_time_travel

Demo mode (the default) reads the recorded fixtures in tests/fixtures/bigquery, which have the
documented shape of jobs.getQueryResults but illustrative values, and prints a verdict labelled
UNVERIFIED-pending-K1. Nothing is measured. Live mode needs a throwaway GCP project (K1) and the
`bq` CLI; it only touches datasets named fa_spike_*:

    --mode live --project P --dataset fa_spike_X --region R                      print the commands
    --mode live --project P --dataset fa_spike_X --region R --action probe --ts T   one probe
    --mode live --project P --dataset fa_spike_X --region R --action schemata       read the window

The reusable function is `bq_time_travel_probe(table, ts, run_query=...)`: "open" while the
time-travel window can still read the table as of ts, "closed" once it cannot. The fail-safe period
is not queryable, so no probe can observe it. The findings and the reasoning are in
docs/adr/bigquery-erasure-probe.md.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "bigquery"

# Everything below is spike-local on purpose: the script is run by hand, once, against a throwaway
# dataset. None of it is a product knob, so none of it goes into Settings.
BQ = "bq"
BQ_TIMEOUT_S = 120.0
# The live path refuses any dataset that does not match this, so it cannot touch real data.
SPIKE_DATASET_RE = re.compile(r"fa_spike_[a-z0-9_]+")
PROJECT_RE = re.compile(r"[a-z][a-z0-9-]*")
REGION_RE = re.compile(r"[a-z][a-z0-9-]*")
NAME_RE = re.compile(r"[A-Za-z0-9_]+")
TABLE_RE = re.compile(r"[a-z][a-z0-9-]*\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+")
DEFAULT_TABLE_NAME = "marker"
INHERIT_SUFFIX = "_inherit"
# 48 h is the documented minimum of the window and the value the arch sets on every dataset.
EXPECTED_HOURS = 48
# BigQuery's error for a snapshot time outside the window. The docs do not state the text, so this
# is UNVERIFIED-pending-K1: both parts must be present to count as "outside the window". The
# variant "Cannot read before ..." is not accepted, it may also mean "before the table existed".
OUTSIDE_WINDOW_HEAD = "invalid snapshot time"
OUTSIDE_WINDOW_TAIL = "time travel window"

OBSERVABLE = "time-travel window observable"
MISMATCH = "mismatch"
UNVERIFIED = "UNVERIFIED-pending-K1"

Probe = Literal["open", "closed"]
RunQuery = Callable[[str], list[dict[str, Any]]]
BqRunner = Callable[[list[str]], str]


class SpikeError(Exception):
    """A probe step failed in a way the operator must see (bad input, missing file, bq failure)."""


class QueryError(SpikeError):
    """BigQuery rejected a query; the message is BigQuery's text, kept for classification."""


@dataclass(frozen=True)
class Verdict:
    verdict: str
    detail: str
    verified: bool

    def line(self) -> str:
        text = f"VERDICT: {self.verdict} ({self.detail})"
        if not self.verified:
            text += f" [{UNVERIFIED}; DEMO FIXTURE, not a measurement]"
        return text


def check_spike_dataset(dataset: str) -> None:
    if not SPIKE_DATASET_RE.fullmatch(dataset):
        raise SpikeError(
            f"dataset {dataset!r} does not match {SPIKE_DATASET_RE.pattern!r}; "
            "the probe only touches throwaway spike datasets"
        )


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.fullmatch(value):
        raise SpikeError(f"{what} {value!r} does not match {pattern.pattern!r}")
    return value


def format_ts(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise SpikeError("the timestamp has no timezone; pass an aware datetime (UTC)")
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def parse_ts(value: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SpikeError(f"{value!r} is not an ISO 8601 timestamp") from exc
    if ts.tzinfo is None:
        raise SpikeError(
            f"{value!r} has no timezone; write it in UTC, for example 2026-11-20T10:00:00Z"
        )
    return ts


def probe_sql(table: str, ts: datetime) -> str:
    """The query text. BigQuery has no identifier parameters, so the table name is validated."""
    _check(TABLE_RE, table, "table name (need project.dataset.table)")
    return f"SELECT COUNT(*) AS n FROM `{table}` FOR SYSTEM_TIME AS OF TIMESTAMP '{format_ts(ts)}'"


def is_outside_window(message: str) -> bool:
    text = message.lower()
    return OUTSIDE_WINDOW_HEAD in text and OUTSIDE_WINDOW_TAIL in text


def _count(rows: list[dict[str, Any]]) -> int:
    if len(rows) != 1 or "n" not in rows[0]:
        raise SpikeError(f"the probe expected one row with a column n, got {rows!r}")
    try:
        count = int(rows[0]["n"])
    except (TypeError, ValueError) as exc:
        raise SpikeError(f"the probe count {rows[0]['n']!r} is not an integer") from exc
    if count < 0:
        raise SpikeError(f"the probe count {count} is negative")
    return count


def bq_time_travel_probe(table: str, ts: datetime, *, run_query: RunQuery) -> Probe:
    """Read `table` as of `ts` through the time-travel window.

    Returns "open" when the read works and finds rows (the data is still recoverable through time
    travel), and "closed" when BigQuery refuses a snapshot time outside the window or the table has
    no rows at ts. Every other failure (permissions, quota, a missing table, a message this code
    does not know) raises and is never mapped to "closed": a false "closed" would sign an erasure
    receipt falsely. The fail-safe period cannot be read by any query, so it is not covered.

    The count is over the whole table: the caller passes a table that holds only the rows under
    test (the scratch marker table here). A row-scoped variant for erasure-verify is a later ticket.
    """
    sql = probe_sql(table, ts)
    try:
        rows = run_query(sql)
    except QueryError as exc:
        if is_outside_window(str(exc)):
            return "closed"
        raise
    return "open" if _count(rows) > 0 else "closed"


def schemata_sql(project: str, region: str, dataset: str) -> str:
    _check(PROJECT_RE, project, "project")
    _check(REGION_RE, region, "region")
    _check(NAME_RE, dataset, "dataset")
    return (
        "SELECT option_value "
        f"FROM `{project}`.`region-{region}`.INFORMATION_SCHEMA.SCHEMATA_OPTIONS "
        f"WHERE schema_name = '{dataset}' AND option_name = 'max_time_travel_hours'"
    )


def read_time_travel_hours(project: str, region: str, dataset: str, *, run_query: RunQuery) -> int:
    rows = run_query(schemata_sql(project, region, dataset))
    if len(rows) != 1 or "option_value" not in rows[0]:
        raise SpikeError(f"no max_time_travel_hours row for dataset {dataset!r}: {rows!r}")
    try:
        return int(rows[0]["option_value"])
    except (TypeError, ValueError) as exc:
        raise SpikeError(
            f"max_time_travel_hours {rows[0]['option_value']!r} is not an integer"
        ) from exc


def compute_verdict(inside: Probe, outside: Probe, hours: int, verified: bool) -> Verdict:
    if inside != "open":
        return Verdict(
            MISMATCH, f"the probe inside the window returned {inside}, expected open", verified
        )
    if outside != "closed":
        return Verdict(
            MISMATCH,
            f"the probe at a timestamp older than the window returned {outside}, expected closed",
            verified,
        )
    if hours != EXPECTED_HOURS:
        return Verdict(
            MISMATCH,
            f"a new dataset reads {hours} h, expected it to inherit the project default "
            f"of {EXPECTED_HOURS} h",
            verified,
        )
    return Verdict(
        OBSERVABLE,
        f"open inside the window, closed outside it, a new dataset inherits {EXPECTED_HOURS} h; "
        "the 7 d fail-safe is not queryable (documented, not measured)",
        verified,
    )


def run_bq(cmd: list[str]) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=BQ_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise SpikeError(f"{cmd[0]} is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SpikeError(f"{cmd[0]} timed out after {BQ_TIMEOUT_S} s") from exc
    except subprocess.CalledProcessError as exc:
        raise QueryError(f"bq failed: {(exc.stderr or '').strip()}") from exc
    return done.stdout


def bq_query_runner(project: str, location: str, runner: BqRunner = run_bq) -> RunQuery:
    _check(PROJECT_RE, project, "project")
    _check(REGION_RE, location, "region")

    def run_query(sql: str) -> list[dict[str, Any]]:
        raw = runner(
            [
                BQ,
                f"--project_id={project}",
                f"--location={location}",
                "query",
                "--nouse_legacy_sql",
                "--format=json",
                sql,
            ]
        )
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpikeError("bq output is not valid JSON") from exc
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SpikeError("bq output is not a JSON list of rows")
        return rows

    return run_query


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpikeError(f"cannot read {path.name}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise SpikeError(f"{path.name} is not valid JSON") from exc


def rows_from_query_results(body: Any) -> list[dict[str, Any]]:
    """Turn a jobs.getQueryResults body (schema.fields + rows[].f[].v) into bq's list of dicts."""
    if not isinstance(body, dict) or "schema" not in body:
        raise SpikeError("not a getQueryResults body with a schema")
    names = [field["name"] for field in body["schema"].get("fields", [])]
    rows: list[dict[str, Any]] = []
    for row in body.get("rows", []):
        cells = row.get("f", [])
        if len(cells) != len(names):
            raise SpikeError("a row has a different number of cells than the schema has fields")
        rows.append({name: cell.get("v") for name, cell in zip(names, cells, strict=True)})
    return rows


def fixture_runner(directory: Path, meta: dict[str, Any]) -> RunQuery:
    """Serve the recorded bodies: by the timestamp in the probe SQL, and for SCHEMATA_OPTIONS."""
    inside = f"'{format_ts(parse_ts(meta['ts_inside']))}'"
    old = f"'{format_ts(parse_ts(meta['ts_old']))}'"

    def run_query(sql: str) -> list[dict[str, Any]]:
        if "SCHEMATA_OPTIONS" in sql:
            name = "schemata_options_inherit.json"
        elif old in sql:
            name = "query_outside_window_error.json"
        elif inside in sql:
            name = "query_inside_window.json"
        else:
            raise SpikeError("no fixture for this query")
        body = read_json(directory / name)
        if isinstance(body, dict) and "error" in body:
            raise QueryError(str(body["error"].get("message", "")))
        return rows_from_query_results(body)

    return run_query


def live_commands(project: str, dataset: str, region: str, table_name: str) -> list[str]:
    """The exact commands of the live run, in order, each quoted with shlex. '#' lines are notes."""
    _check(PROJECT_RE, project, "project")
    _check(REGION_RE, region, "region")
    check_spike_dataset(dataset)
    _check(NAME_RE, table_name, "table name")
    inherit = f"{dataset}{INHERIT_SUFFIX}"
    table = f"{project}.{dataset}.{table_name}"

    def bq(*args: str) -> str:
        return shlex.join([BQ, f"--project_id={project}", f"--location={region}", *args])

    def sql(text: str) -> str:
        return bq("query", "--nouse_legacy_sql", text)

    def spike(*args: str) -> str:
        base = ["--mode", "live", "--project", project, "--region", region]
        return shlex.join(["python", "-m", "scripts.spike_bigquery_time_travel", *base, *args])

    return [
        "# 1. project default for new datasets, per region (UNVERIFIED-pending-K1: exact syntax)",
        sql(
            f"ALTER PROJECT `{project}` "
            f"SET OPTIONS (`region-{region}.default_max_time_travel_hours` = {EXPECTED_HOURS})"
        ),
        "# 2. scratch dataset with the minimum window set explicitly",
        bq("mk", "--dataset", f"--max_time_travel_hours={EXPECTED_HOURS}", f"{project}:{dataset}"),
        "# 3. a dataset created with NO options: it must read 48 (acceptance criterion 2)",
        bq("mk", "--dataset", f"{project}:{inherit}"),
        "# 4. table and one marker row",
        sql(f"CREATE TABLE `{table}` (id INT64, note STRING)"),
        sql(f"INSERT INTO `{table}` (id, note) VALUES (1, 'marker')"),
        "# 5. t_before_delete from BigQuery's own clock (no skew); copy the printed value",
        sql("SELECT FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E6SZ', CURRENT_TIMESTAMP()) AS t"),
        sql(f"DELETE FROM `{table}` WHERE id = 1"),
        "# 6. probe inside the window, expect: -> open",
        spike("--dataset", dataset, "--action", "probe", "--ts", "<t_before_delete>"),
        f"# 7. wait more than {EXPECTED_HOURS} h after step 5 plus a margin. {EXPECTED_HOURS} h is "
        "the documented minimum window, so no shorter run exists",
        "# 8. the same timestamp again, expect: -> closed (refused as outside the window)",
        spike("--dataset", dataset, "--action", "probe", "--ts", "<t_before_delete>"),
        "# 9. INFORMATION_SCHEMA.SCHEMATA_OPTIONS of the dataset made with no options, expect 48",
        spike("--dataset", inherit, "--action", "schemata"),
        "# 10. clean up",
        bq("rm", "-r", "-f", "-d", f"{project}:{dataset}"),
        bq("rm", "-r", "-f", "-d", f"{project}:{inherit}"),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BigQuery time-travel probe (E12-T2 spike)")
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--action", choices=("commands", "probe", "schemata"), default="commands")
    parser.add_argument("--project", help="live: the throwaway GCP project (K1)")
    parser.add_argument("--dataset", help="live: a throwaway dataset, fa_spike_*")
    parser.add_argument("--region", help="live: the dataset location, for example europe-west2")
    parser.add_argument("--table", default=DEFAULT_TABLE_NAME, help="live, action probe")
    parser.add_argument("--ts", help="live, action probe: ISO 8601 timestamp with a timezone")
    parser.add_argument("--fixtures-dir", type=Path, default=None, help="demo: recorded bodies")
    return parser


def evaluate_demo(directory: Path) -> int:
    meta = read_json(directory / "meta.json")
    try:
        table, region, inherit = meta["table"], meta["region"], meta["inherit_dataset"]
        run_query = fixture_runner(directory, meta)
        inside_ts, old_ts = parse_ts(meta["ts_inside"]), parse_ts(meta["ts_old"])
    except (KeyError, TypeError) as exc:
        raise SpikeError(f"meta.json is missing a field: {exc}") from exc
    inside = bq_time_travel_probe(table, inside_ts, run_query=run_query)
    outside = bq_time_travel_probe(table, old_ts, run_query=run_query)
    hours = read_time_travel_hours(table.split(".")[0], region, inherit, run_query=run_query)
    result = compute_verdict(inside, outside, hours, verified=False)
    report = {
        "demo": True,
        "note": meta.get("note"),
        "table": table,
        "probes": {"inside_window": inside, "older_than_window": outside},
        "max_time_travel_hours": {inherit: hours},
    }
    sys.stdout.write(json.dumps(report, indent=2) + "\n" + result.line() + "\n")
    return 0 if result.verdict == OBSERVABLE else 1


def run_live(parser: argparse.ArgumentParser, args: argparse.Namespace, runner: BqRunner) -> int:
    if not args.project or not args.dataset or not args.region:
        parser.error("--mode live needs --project, --dataset and --region")
    check_spike_dataset(args.dataset)
    if args.action == "commands":
        cmds = live_commands(args.project, args.dataset, args.region, args.table)
        sys.stdout.write("\n".join(cmds) + "\n")
        return 0
    run_query = bq_query_runner(args.project, args.region, runner)
    if args.action == "probe":
        if not args.ts:
            parser.error("--action probe needs --ts")
        ts = parse_ts(args.ts)
        table = f"{args.project}.{args.dataset}.{args.table}"
        state = bq_time_travel_probe(table, ts, run_query=run_query)
        sys.stdout.write(f"PROBE: {table} at {ts.astimezone(UTC).isoformat()} -> {state}\n")
        return 0
    hours = read_time_travel_hours(args.project, args.region, args.dataset, run_query=run_query)
    ok = hours == EXPECTED_HOURS
    sys.stdout.write(
        f"dataset {args.dataset}: max_time_travel_hours = {hours} "
        f"(expected {EXPECTED_HOURS}: {'ok' if ok else 'MISMATCH'})\n"
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None, runner: BqRunner = run_bq) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.mode == "demo":
            return evaluate_demo(args.fixtures_dir or DEFAULT_FIXTURES_DIR)
        return run_live(parser, args, runner)
    except SpikeError as exc:
        log.error("spike_failed", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
