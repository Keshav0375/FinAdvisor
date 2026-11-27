from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from src.config import Settings
from src.errors import FinAdvisorError
from src.lifecycle import bq_probe

TABLE = "fa-spike-demo.fa_spike_tt.marker"
TS = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
OUTSIDE_MSG = "Invalid snapshot time 1763373600000 for table p:d.t, outside of time travel window."


def rows_runner(rows: list[dict[str, Any]]) -> Callable[[str], list[dict[str, Any]]]:
    return lambda sql: rows


def failing_runner(message: str) -> Callable[[str], list[dict[str, Any]]]:
    def run_query(sql: str) -> list[dict[str, Any]]:
        raise bq_probe.QueryError(message)

    return run_query


# AC1: the three results. "closed" is the window-refused error and nothing else.
def test_probe_open_when_row_visible_inside_window() -> None:
    result = bq_probe.bq_time_travel_probe(TABLE, TS, run_query=rows_runner([{"n": "1"}]))
    assert result == "open"


def test_probe_open_empty_when_no_rows_at_ts_is_not_closed() -> None:
    result = bq_probe.bq_time_travel_probe(TABLE, TS, run_query=rows_runner([{"n": "0"}]))
    assert result == "open_empty"
    assert result != "closed"


def test_probe_accepts_integer_count() -> None:
    assert bq_probe.bq_time_travel_probe(TABLE, TS, run_query=rows_runner([{"n": 2}])) == "open"


def test_probe_closed_only_on_outside_window_error() -> None:
    result = bq_probe.bq_time_travel_probe(TABLE, TS, run_query=failing_runner(OUTSIDE_MSG))
    assert result == "closed"


def test_probe_outside_window_match_ignores_case() -> None:
    run_query = failing_runner(OUTSIDE_MSG.upper())
    assert bq_probe.bq_time_travel_probe(TABLE, TS, run_query=run_query) == "closed"


@pytest.mark.parametrize(
    "message",
    [
        "Access Denied: Table p:d.t: Permission bigquery.tables.getData denied",
        "Not found: Table p:d.t was not found in location EU",
        "Invalid snapshot time 1 for table p:d.t. Cannot read before 2",
        "Invalid snapshot time 1 for table p:d.t, in the future",
        "Quota exceeded: Your project exceeded quota for concurrent queries",
        "",
    ],
)
def test_probe_raises_on_unknown_error_never_closed(message: str) -> None:
    with pytest.raises(bq_probe.QueryError):
        bq_probe.bq_time_travel_probe(TABLE, TS, run_query=failing_runner(message))


@pytest.mark.parametrize(
    "rows",
    [[], [{"n": "1"}, {"n": "1"}], [{"x": "1"}], [{"n": "many"}], [{"n": None}], [{"n": "-1"}]],
)
def test_probe_raises_on_bad_result_shape(rows: list[dict[str, Any]]) -> None:
    with pytest.raises(bq_probe.BqProbeError):
        bq_probe.bq_time_travel_probe(TABLE, TS, run_query=rows_runner(rows))


def test_errors_belong_to_the_app_hierarchy() -> None:
    assert issubclass(bq_probe.BqProbeError, FinAdvisorError)
    assert issubclass(bq_probe.QueryError, bq_probe.BqProbeError)


def test_probe_rejects_naive_ts() -> None:
    with pytest.raises(bq_probe.BqProbeError, match="timezone"):
        bq_probe.bq_time_travel_probe(
            TABLE, datetime(2026, 11, 20, 10, 0, 0), run_query=rows_runner([{"n": "1"}])
        )


@pytest.mark.parametrize(
    "table",
    ["p.d", "p.d.t.x", "p.d.t`; DROP TABLE x; --", "p.d.t\n", "p. d.t", "P.d.t", "p.d.t'", ""],
)
def test_probe_rejects_bad_table_name(table: str) -> None:
    called: list[str] = []

    def run_query(sql: str) -> list[dict[str, Any]]:
        called.append(sql)
        return [{"n": "1"}]

    with pytest.raises(bq_probe.BqProbeError, match="table"):
        bq_probe.bq_time_travel_probe(table, TS, run_query=run_query)
    assert called == []


def test_probe_sql_has_backticked_table_and_utc_system_time() -> None:
    seen: list[str] = []

    def run_query(sql: str) -> list[dict[str, Any]]:
        seen.append(sql)
        return [{"n": "1"}]

    ts = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    bq_probe.bq_time_travel_probe(TABLE, ts, run_query=run_query)
    assert seen == [
        f"SELECT COUNT(*) AS n FROM `{TABLE}` "
        "FOR SYSTEM_TIME AS OF TIMESTAMP '2026-11-20T10:00:00.000000+00:00'"
    ]


# AC2: SCHEMATA_OPTIONS read.
def test_schemata_sql_scopes_region_dataset_and_option() -> None:
    sql = bq_probe.schemata_sql("fa-spike-demo", "europe-west2", "fa_spike_tt_inherit")
    assert "`fa-spike-demo`.`region-europe-west2`.INFORMATION_SCHEMA.SCHEMATA_OPTIONS" in sql
    assert "schema_name = 'fa_spike_tt_inherit'" in sql
    assert "option_name = 'max_time_travel_hours'" in sql


def test_read_time_travel_hours_returns_int() -> None:
    hours = bq_probe.read_time_travel_hours(
        "fa-spike-demo",
        "europe-west2",
        "fa_spike_tt",
        run_query=rows_runner([{"option_value": "48"}]),
    )
    assert hours == bq_probe.EXPECTED_HOURS


@pytest.mark.parametrize(
    "rows", [[], [{"option_value": "x"}], [{"other": "48"}], [{"option_value": None}]]
)
def test_read_time_travel_hours_raises_on_bad_rows(rows: list[dict[str, Any]]) -> None:
    with pytest.raises(bq_probe.BqProbeError):
        bq_probe.read_time_travel_hours(
            "fa-spike-demo", "us", "fa_spike_tt", run_query=rows_runner(rows)
        )


@pytest.mark.parametrize(
    ("project", "region", "dataset"),
    [
        ("fa-spike-demo", "us`", "fa_spike_tt"),
        ("fa-spike-demo", "EU", "fa_spike_tt"),
        ("Fa Spike", "us", "fa_spike_tt"),
        ("fa-spike-demo", "us", "fa_spike_tt'; --"),
    ],
)
def test_schemata_sql_rejects_unsafe_names(project: str, region: str, dataset: str) -> None:
    with pytest.raises(bq_probe.BqProbeError):
        bq_probe.schemata_sql(project, region, dataset)


# run_bq: argv, no shell, timeout from the caller, error mapping.
class Done:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_run_bq_uses_argv_without_shell_and_the_given_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return Done("[]")

    monkeypatch.setattr(bq_probe.subprocess, "run", fake_run)
    assert bq_probe.run_bq(["bq", "ls"], 7.5) == "[]"
    assert seen["cmd"] == ["bq", "ls"]
    assert seen["kwargs"]["timeout"] == 7.5
    assert not seen["kwargs"].get("shell", False)
    assert seen["kwargs"]["check"] is True


def test_run_bq_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise FileNotFoundError("bq")

    monkeypatch.setattr(bq_probe.subprocess, "run", fake_run)
    with pytest.raises(bq_probe.BqProbeError, match="not installed"):
        bq_probe.run_bq(["bq", "ls"], 1.0)


def test_run_bq_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(bq_probe.subprocess, "run", fake_run)
    with pytest.raises(bq_probe.BqProbeError, match="timed out after 1.0 s"):
        bq_probe.run_bq(["bq", "ls"], 1.0)


def test_run_bq_failure_becomes_query_error_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise subprocess.CalledProcessError(1, cmd, stderr=f"BigQuery error: {OUTSIDE_MSG}\n")

    monkeypatch.setattr(bq_probe.subprocess, "run", fake_run)
    with pytest.raises(bq_probe.QueryError, match="outside of time travel window"):
        bq_probe.run_bq(["bq", "query"], 1.0)


def test_run_bq_failure_with_no_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise subprocess.CalledProcessError(1, cmd, stderr=None)

    monkeypatch.setattr(bq_probe.subprocess, "run", fake_run)
    with pytest.raises(bq_probe.QueryError):
        bq_probe.run_bq(["bq", "query"], 1.0)


# bq_query_runner: the binary and the timeout are the Settings values.
def test_bq_query_runner_builds_argv_with_the_configured_binary_and_parses_json() -> None:
    seen: list[list[str]] = []

    def runner(cmd: list[str]) -> str:
        seen.append(cmd)
        return '[{"n": "1"}]'

    run_query = bq_probe.bq_query_runner(
        "k1-project", "europe-west2", bq_bin="/opt/bq", timeout_s=5.0, runner=runner
    )
    assert run_query("SELECT 1") == [{"n": "1"}]
    assert seen == [
        [
            "/opt/bq",
            "--project_id=k1-project",
            "--location=europe-west2",
            "query",
            "--nouse_legacy_sql",
            "--format=json",
            "SELECT 1",
        ]
    ]


def test_bq_query_runner_default_runner_passes_binary_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        seen["cmd"] = cmd
        seen["timeout"] = kwargs["timeout"]
        return Done("[]")

    monkeypatch.setattr(bq_probe.subprocess, "run", fake_run)
    run_query = bq_probe.bq_query_runner("k1-project", "us", bq_bin="my-bq", timeout_s=33.0)
    assert run_query("SELECT 1") == []
    assert seen["cmd"][0] == "my-bq"
    assert seen["timeout"] == 33.0


@pytest.mark.parametrize("raw", ["not json", '{"n": 1}', '["x"]', ""])
def test_bq_query_runner_rejects_non_row_output(raw: str) -> None:
    run_query = bq_probe.bq_query_runner(
        "k1-project", "us", bq_bin="bq", timeout_s=1.0, runner=lambda cmd: raw
    )
    with pytest.raises(bq_probe.BqProbeError):
        run_query("SELECT 1")


def test_bq_query_runner_rejects_unsafe_names() -> None:
    with pytest.raises(bq_probe.BqProbeError):
        bq_probe.bq_query_runner("P;x", "us", bq_bin="bq", timeout_s=1.0)
    with pytest.raises(bq_probe.BqProbeError):
        bq_probe.bq_query_runner("k1-project", "US`", bq_bin="bq", timeout_s=1.0)


# Config knobs: defaults and env overrides (Settings, .env.example).
def test_bq_settings_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()  # type: ignore[call-arg]
    assert settings.bq_bin == "bq"
    assert settings.bq_timeout_s == 120.0
    monkeypatch.setenv("BQ_BIN", "/usr/local/bin/bq")
    monkeypatch.setenv("BQ_TIMEOUT_S", "300")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.bq_bin == "/usr/local/bin/bq"
    assert settings.bq_timeout_s == 300.0


@pytest.mark.parametrize(
    ("name", "bad"), [("BQ_TIMEOUT_S", "0"), ("BQ_TIMEOUT_S", "-1"), ("BQ_BIN", "")]
)
def test_bq_settings_reject_bad_values(
    monkeypatch: pytest.MonkeyPatch, name: str, bad: str
) -> None:
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=name):
        Settings()  # type: ignore[call-arg]
