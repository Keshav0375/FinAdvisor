"""E12-T2 spike: is the BigQuery time-travel window observable, and can erasure-verify probe it?

Run by hand, never in the PR gate, as a module from backend/ (not `python scripts/...`):

    cd backend && python -m scripts.spike_bigquery_time_travel

A thin CLI over src/lifecycle/bq_probe.py, where the reusable probe lives. Demo mode (the default)
reads the recorded fixtures in tests/fixtures/bigquery, which have the documented shape of
jobs.getQueryResults but illustrative values, and prints a verdict labelled UNVERIFIED-pending-K1.
Nothing is measured. Live mode needs a throwaway GCP project (K1) and the `bq` CLI; it only touches
datasets named fa_spike_*. The binary and the timeout come from BQ_BIN and BQ_TIMEOUT_S
(Settings.bq_bin and Settings.bq_timeout_s, same defaults):

    --mode live --project P --dataset fa_spike_X --region R                      print the commands
    --mode live --project P --dataset fa_spike_X --region R --action probe --ts T   one probe
    --mode live --project P --dataset fa_spike_X --region R --action schemata       read the window

The findings and the reasoning are in docs/adr/bigquery-erasure-probe.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

import structlog
from pydantic import TypeAdapter, ValidationError

from src.config import Settings
from src.lifecycle.bq_probe import (
    EXPECTED_HOURS,
    NAME_RE,
    PROJECT_RE,
    REGION_RE,
    BqProbeError,
    BqRunner,
    ProbeResult,
    QueryError,
    RunQuery,
    bq_query_runner,
    bq_time_travel_probe,
    check_pattern,
    format_ts,
    read_time_travel_hours,
)

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "bigquery"

# The live path refuses any dataset that does not match this, so it cannot touch real data.
SPIKE_DATASET_RE = re.compile(r"fa_spike_[a-z0-9_]+")
DEFAULT_TABLE_NAME = "marker"
INHERIT_SUFFIX = "_inherit"

OBSERVABLE = "time-travel window observable"
MISMATCH = "mismatch"
UNVERIFIED = "UNVERIFIED-pending-K1"


class SpikeError(Exception):
    """A spike step failed in a way the operator must see (bad CLI input, missing fixture)."""


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


def configured[T](
    field: str, env_name: str, kind: type[T], environ: Mapping[str, str] | None = None
) -> T:
    """A Settings field read from its env var with the field's own constraints, else its default.

    Settings is not built: it would need every secret for a script that only drives `bq`.
    """
    env = os.environ if environ is None else environ
    info = Settings.model_fields[field]
    raw = env.get(env_name)
    if raw is None:
        return cast(T, info.default)
    adapter: TypeAdapter[T] = TypeAdapter(Annotated[kind, *info.metadata])
    try:
        return adapter.validate_python(raw)
    except ValidationError as exc:
        raise SpikeError(f"{env_name}={raw!r} is invalid: {exc.errors()[0]['msg']}") from exc


def check_spike_dataset(dataset: str) -> None:
    if not SPIKE_DATASET_RE.fullmatch(dataset):
        raise SpikeError(
            f"dataset {dataset!r} does not match {SPIKE_DATASET_RE.pattern!r}; "
            "the probe only touches throwaway spike datasets"
        )


def parse_ts(value: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SpikeError(f"{value!r} is not an ISO 8601 timestamp") from exc
    if ts.tzinfo is None:
        raise SpikeError(f"{value!r} has no timezone; write it in UTC, like 2026-11-20T10:00:00Z")
    return ts


def compute_verdict(
    inside: ProbeResult, outside: ProbeResult, hours: int, verified: bool
) -> Verdict:
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


def live_commands(
    project: str, dataset: str, region: str, table_name: str, bq_bin: str
) -> list[str]:
    """The exact commands of the live run, in order, each quoted with shlex. '#' lines are notes."""
    check_pattern(PROJECT_RE, project, "project")
    check_pattern(REGION_RE, region, "region")
    check_spike_dataset(dataset)
    check_pattern(NAME_RE, table_name, "table name")
    inherit = f"{dataset}{INHERIT_SUFFIX}"
    table = f"{project}.{dataset}.{table_name}"

    def bq(*args: str) -> str:
        return shlex.join([bq_bin, f"--project_id={project}", f"--location={region}", *args])

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


def run_live(
    parser: argparse.ArgumentParser, args: argparse.Namespace, runner: BqRunner | None
) -> int:
    if not args.project or not args.dataset or not args.region:
        parser.error("--mode live needs --project, --dataset and --region")
    check_spike_dataset(args.dataset)
    bq_bin = configured("bq_bin", "BQ_BIN", str)
    timeout_s = configured("bq_timeout_s", "BQ_TIMEOUT_S", float)
    if args.action == "commands":
        cmds = live_commands(args.project, args.dataset, args.region, args.table, bq_bin)
        sys.stdout.write("\n".join(cmds) + "\n")
        return 0
    run_query = bq_query_runner(
        args.project, args.region, bq_bin=bq_bin, timeout_s=timeout_s, runner=runner
    )
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


def main(argv: list[str] | None = None, runner: BqRunner | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.mode == "demo":
            return evaluate_demo(args.fixtures_dir or DEFAULT_FIXTURES_DIR)
        return run_live(parser, args, runner)
    except (SpikeError, BqProbeError) as exc:
        log.error("spike_failed", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
