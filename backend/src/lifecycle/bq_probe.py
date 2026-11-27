"""BigQuery time-travel probe for erasure-verify (ADR-030, spike E12-T2).

Only the 48 h time-travel window is observable. The 7 d fail-safe after it is not queryable
(https://docs.cloud.google.com/bigquery/docs/time-travel), so no probe here covers it; the receipt
states it as a `documented` window. Findings: docs/adr/bigquery-erasure-probe.md.

The query runner is injected (`RunQuery`), so the probe has no client dependency and a test or a
second implementation can replace the `bq` CLI runner below.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from src.errors import FinAdvisorError

# Names go into SQL text (BigQuery has no identifier parameters), so each is matched in full.
PROJECT_RE = re.compile(r"[a-z][a-z0-9-]*")
REGION_RE = re.compile(r"[a-z][a-z0-9-]*")
NAME_RE = re.compile(r"[A-Za-z0-9_]+")
TABLE_RE = re.compile(r"[a-z][a-z0-9-]*\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+")

# 48 h is the documented minimum of the window and the value the arch sets on every dataset.
EXPECTED_HOURS = 48

# BigQuery's error for a snapshot time outside the window. The docs do not state the text, so this
# is UNVERIFIED-pending-K1: both parts must be present to count as "outside the window". The
# variant "Cannot read before ..." is not accepted, it may also mean "before the table existed".
OUTSIDE_WINDOW_HEAD = "invalid snapshot time"
OUTSIDE_WINDOW_TAIL = "time travel window"

# "open": the window still reads the table at ts and rows are there (data is recoverable).
# "open_empty": the window still reads the table at ts but there are no rows (for example ts is
#   after the delete): this says nothing about the window closing, so it is never a closed signal.
# "closed": BigQuery refused the snapshot time as outside the window. Only this error maps to it.
ProbeResult = Literal["open", "open_empty", "closed"]
RunQuery = Callable[[str], list[dict[str, Any]]]
BqRunner = Callable[[list[str]], str]


class BqProbeError(FinAdvisorError):
    """A probe step failed in a way the caller must see (bad input, missing binary, bq failure)."""


class QueryError(BqProbeError):
    """BigQuery rejected a query; the message is BigQuery's text, kept for classification."""


def check_pattern(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.fullmatch(value):
        raise BqProbeError(f"{what} {value!r} does not match {pattern.pattern!r}")
    return value


def format_ts(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise BqProbeError("the timestamp has no timezone; pass an aware datetime (UTC)")
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def probe_sql(table: str, ts: datetime) -> str:
    """The query text; the table name is validated because it is interpolated."""
    check_pattern(TABLE_RE, table, "table name (need project.dataset.table)")
    return f"SELECT COUNT(*) AS n FROM `{table}` FOR SYSTEM_TIME AS OF TIMESTAMP '{format_ts(ts)}'"


def is_outside_window(message: str) -> bool:
    text = message.lower()
    return OUTSIDE_WINDOW_HEAD in text and OUTSIDE_WINDOW_TAIL in text


def _count(rows: list[dict[str, Any]]) -> int:
    if len(rows) != 1 or "n" not in rows[0]:
        raise BqProbeError(f"the probe expected one row with a column n, got {rows!r}")
    try:
        count = int(rows[0]["n"])
    except (TypeError, ValueError) as exc:
        raise BqProbeError(f"the probe count {rows[0]['n']!r} is not an integer") from exc
    if count < 0:
        raise BqProbeError(f"the probe count {count} is negative")
    return count


def bq_time_travel_probe(table: str, ts: datetime, *, run_query: RunQuery) -> ProbeResult:
    """Read `table` as of `ts` through the time-travel window.

    - "open": the read works and finds rows, so the data is still recoverable through time travel.
    - "open_empty": the read works and finds no rows at ts. The window is still open for this
      timestamp; ts may be after the delete or before the data arrived. Not a closed signal.
    - "closed": BigQuery refuses the snapshot time as outside the window (the one error text in
      OUTSIDE_WINDOW_HEAD and OUTSIDE_WINDOW_TAIL). Nothing else maps to "closed".

    Every other failure (permissions, quota, a missing table, a message this code does not know)
    raises and is never mapped to "closed": a false "closed" would sign an erasure receipt falsely.
    erasure-verify signs the window only on "closed", probing a ts just before the delete. The
    fail-safe period cannot be read by any query, so it is not covered.

    The count is over the whole table: the caller passes a table that holds only the rows under
    test (the scratch marker table in the spike). A row-scoped variant is a later ticket.
    """
    sql = probe_sql(table, ts)
    try:
        rows = run_query(sql)
    except QueryError as exc:
        if is_outside_window(str(exc)):
            return "closed"
        raise
    return "open" if _count(rows) > 0 else "open_empty"


def schemata_sql(project: str, region: str, dataset: str) -> str:
    check_pattern(PROJECT_RE, project, "project")
    check_pattern(REGION_RE, region, "region")
    check_pattern(NAME_RE, dataset, "dataset")
    return (
        "SELECT option_value "
        f"FROM `{project}`.`region-{region}`.INFORMATION_SCHEMA.SCHEMATA_OPTIONS "
        f"WHERE schema_name = '{dataset}' AND option_name = 'max_time_travel_hours'"
    )


def read_time_travel_hours(project: str, region: str, dataset: str, *, run_query: RunQuery) -> int:
    rows = run_query(schemata_sql(project, region, dataset))
    if len(rows) != 1 or "option_value" not in rows[0]:
        raise BqProbeError(f"no max_time_travel_hours row for dataset {dataset!r}: {rows!r}")
    try:
        return int(rows[0]["option_value"])
    except (TypeError, ValueError) as exc:
        raise BqProbeError(
            f"max_time_travel_hours {rows[0]['option_value']!r} is not an integer"
        ) from exc


def run_bq(cmd: list[str], timeout_s: float) -> str:
    """Run `cmd` as an argv list (no shell). The timeout comes from Settings.bq_timeout_s."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
    except FileNotFoundError as exc:
        raise BqProbeError(f"{cmd[0]} is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise BqProbeError(f"{cmd[0]} timed out after {timeout_s} s") from exc
    except subprocess.CalledProcessError as exc:
        raise QueryError(f"bq failed: {(exc.stderr or '').strip()}") from exc
    return done.stdout


def bq_query_runner(
    project: str,
    location: str,
    *,
    bq_bin: str,
    timeout_s: float,
    runner: BqRunner | None = None,
) -> RunQuery:
    """A RunQuery that shells out to the `bq` CLI. `bq_bin` and `timeout_s` are the Settings
    fields `bq_bin` and `bq_timeout_s`; `runner` replaces the subprocess call in tests."""
    check_pattern(PROJECT_RE, project, "project")
    check_pattern(REGION_RE, location, "region")
    run = runner if runner is not None else (lambda cmd: run_bq(cmd, timeout_s))

    def run_query(sql: str) -> list[dict[str, Any]]:
        raw = run(
            [
                bq_bin,
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
            raise BqProbeError("bq output is not valid JSON") from exc
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise BqProbeError("bq output is not a JSON list of rows")
        return rows

    return run_query
