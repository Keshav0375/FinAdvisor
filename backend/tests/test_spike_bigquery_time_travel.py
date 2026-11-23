from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import spike_bigquery_time_travel as spike

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bigquery"
ADR = Path(__file__).resolve().parents[2] / "docs" / "adr" / "bigquery-erasure-probe.md"
TABLE = "fa-spike-demo.fa_spike_tt.marker"
TS = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
OUTSIDE_MSG = "Invalid snapshot time 1763373600000 for table p:d.t, outside of time travel window."


def rows_runner(rows: list[dict[str, Any]]) -> Callable[[str], list[dict[str, Any]]]:
    return lambda sql: rows


def failing_runner(message: str) -> Callable[[str], list[dict[str, Any]]]:
    def run_query(sql: str) -> list[dict[str, Any]]:
        raise spike.QueryError(message)

    return run_query


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# AC1: probe open inside the window, closed outside it.
def test_probe_open_when_row_visible_inside_window() -> None:
    assert spike.bq_time_travel_probe(TABLE, TS, run_query=rows_runner([{"n": "1"}])) == "open"


def test_probe_closed_when_row_absent() -> None:
    assert spike.bq_time_travel_probe(TABLE, TS, run_query=rows_runner([{"n": "0"}])) == "closed"


def test_probe_accepts_integer_count() -> None:
    assert spike.bq_time_travel_probe(TABLE, TS, run_query=rows_runner([{"n": 2}])) == "open"


def test_probe_closed_when_outside_window_error() -> None:
    assert spike.bq_time_travel_probe(TABLE, TS, run_query=failing_runner(OUTSIDE_MSG)) == "closed"


def test_probe_outside_window_match_ignores_case() -> None:
    message = OUTSIDE_MSG.upper()
    assert spike.bq_time_travel_probe(TABLE, TS, run_query=failing_runner(message)) == "closed"


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
    with pytest.raises(spike.QueryError):
        spike.bq_time_travel_probe(TABLE, TS, run_query=failing_runner(message))


@pytest.mark.parametrize(
    "rows",
    [[], [{"n": "1"}, {"n": "1"}], [{"x": "1"}], [{"n": "many"}], [{"n": None}], [{"n": "-1"}]],
)
def test_probe_raises_on_bad_result_shape(rows: list[dict[str, Any]]) -> None:
    with pytest.raises(spike.SpikeError):
        spike.bq_time_travel_probe(TABLE, TS, run_query=rows_runner(rows))


def test_probe_rejects_naive_ts() -> None:
    with pytest.raises(spike.SpikeError, match="timezone"):
        spike.bq_time_travel_probe(
            TABLE, datetime(2026, 11, 20, 10, 0, 0), run_query=rows_runner([{"n": "1"}])
        )


@pytest.mark.parametrize(
    "table",
    [
        "p.d",
        "p.d.t.x",
        "p.d.t`; DROP TABLE x; --",
        "p.d.t\n",
        "p. d.t",
        "P.d.t",
        "p.d.t'",
        "",
    ],
)
def test_probe_rejects_bad_table_name(table: str) -> None:
    called: list[str] = []

    def run_query(sql: str) -> list[dict[str, Any]]:
        called.append(sql)
        return [{"n": "1"}]

    with pytest.raises(spike.SpikeError, match="table"):
        spike.bq_time_travel_probe(table, TS, run_query=run_query)
    assert called == []


def test_probe_sql_has_backticked_table_and_utc_system_time() -> None:
    seen: list[str] = []

    def run_query(sql: str) -> list[dict[str, Any]]:
        seen.append(sql)
        return [{"n": "1"}]

    offset = timezone(timedelta(hours=2))
    spike.bq_time_travel_probe(
        TABLE, datetime(2026, 11, 20, 12, 0, 0, tzinfo=offset), run_query=run_query
    )
    assert seen == [
        f"SELECT COUNT(*) AS n FROM `{TABLE}` "
        "FOR SYSTEM_TIME AS OF TIMESTAMP '2026-11-20T10:00:00.000000+00:00'"
    ]


# AC2: SCHEMATA_OPTIONS read.
def test_schemata_sql_scopes_region_dataset_and_option() -> None:
    sql = spike.schemata_sql("fa-spike-demo", "europe-west2", "fa_spike_tt_inherit")
    assert "`fa-spike-demo`.`region-europe-west2`.INFORMATION_SCHEMA.SCHEMATA_OPTIONS" in sql
    assert "schema_name = 'fa_spike_tt_inherit'" in sql
    assert "option_name = 'max_time_travel_hours'" in sql


def test_read_time_travel_hours_returns_int() -> None:
    hours = spike.read_time_travel_hours(
        "fa-spike-demo",
        "europe-west2",
        "fa_spike_tt",
        run_query=rows_runner([{"option_value": "48"}]),
    )
    assert hours == 48


@pytest.mark.parametrize(
    "rows", [[], [{"option_value": "x"}], [{"other": "48"}], [{"option_value": None}]]
)
def test_read_time_travel_hours_raises_on_bad_rows(rows: list[dict[str, Any]]) -> None:
    with pytest.raises(spike.SpikeError):
        spike.read_time_travel_hours(
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
    with pytest.raises(spike.SpikeError):
        spike.schemata_sql(project, region, dataset)


# Parsing of the recorded REST bodies.
def test_rows_from_query_results_maps_schema_to_values() -> None:
    assert spike.rows_from_query_results(load("query_inside_window.json")) == [{"n": "1"}]


def test_rows_from_query_results_without_rows_is_empty() -> None:
    body = {"schema": {"fields": [{"name": "n"}]}, "totalRows": "0"}
    assert spike.rows_from_query_results(body) == []


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"rows": [{"f": [{"v": "1"}]}]},
        {"schema": {"fields": [{"name": "n"}]}, "rows": [{"f": []}]},
    ],
)
def test_rows_from_query_results_rejects_odd_bodies(body: Any) -> None:
    with pytest.raises(spike.SpikeError):
        spike.rows_from_query_results(body)


def test_fixture_runner_serves_inside_outside_and_schemata() -> None:
    meta = load("meta.json")
    run_query = spike.fixture_runner(FIXTURES, meta)
    assert spike.bq_time_travel_probe(TABLE, TS, run_query=run_query) == "open"
    old = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)
    assert spike.bq_time_travel_probe(TABLE, old, run_query=run_query) == "closed"
    assert (
        spike.read_time_travel_hours(
            "fa-spike-demo", "europe-west2", "fa_spike_tt_inherit", run_query=run_query
        )
        == 48
    )


def test_fixture_runner_has_no_answer_for_unknown_timestamp() -> None:
    run_query = spike.fixture_runner(FIXTURES, load("meta.json"))
    with pytest.raises(spike.SpikeError, match="no fixture"):
        spike.bq_time_travel_probe(TABLE, datetime(2020, 1, 1, tzinfo=UTC), run_query=run_query)


def test_fixture_runner_missing_file_raises(tmp_path: Path) -> None:
    run_query = spike.fixture_runner(tmp_path, load("meta.json"))
    with pytest.raises(spike.SpikeError, match="cannot read"):
        spike.bq_time_travel_probe(TABLE, TS, run_query=run_query)


def test_fixture_runner_bad_json_raises(tmp_path: Path) -> None:
    (tmp_path / "query_inside_window.json").write_text("not json", encoding="utf-8")
    run_query = spike.fixture_runner(tmp_path, load("meta.json"))
    with pytest.raises(spike.SpikeError, match="not valid JSON"):
        spike.bq_time_travel_probe(TABLE, TS, run_query=run_query)


# Verdict.
def test_verdict_observable_when_all_three_hold() -> None:
    result = spike.compute_verdict("open", "closed", 48, verified=True)
    assert result.verdict == spike.OBSERVABLE
    assert "not queryable" in result.detail
    assert spike.UNVERIFIED not in result.line()


def test_verdict_demo_line_is_labelled() -> None:
    line = spike.compute_verdict("open", "closed", 48, verified=False).line()
    assert spike.UNVERIFIED in line
    assert "DEMO FIXTURE" in line


@pytest.mark.parametrize(
    ("inside", "outside", "hours", "needle"),
    [
        ("closed", "closed", 48, "inside the window"),
        ("open", "open", 48, "older than the window"),
        ("open", "closed", 72, "inherit"),
    ],
)
def test_verdict_mismatch_names_the_failed_check(
    inside: str, outside: str, hours: int, needle: str
) -> None:
    result = spike.compute_verdict(inside, outside, hours, verified=True)  # type: ignore[arg-type]
    assert result.verdict == spike.MISMATCH
    assert needle in result.detail


# Printed commands.
def test_live_commands_are_shlex_quoted_and_cover_the_flow() -> None:
    lines = spike.live_commands("k1-project", "fa_spike_tt", "europe-west2", "marker")
    commands = [line for line in lines if not line.startswith("#")]
    text = "\n".join(lines)
    assert "ALTER PROJECT" in text
    assert "default_max_time_travel_hours" in text
    assert "--max_time_travel_hours=48" in text
    assert "fa_spike_tt_inherit" in text
    assert "DELETE FROM" in text
    assert "SCHEMATA_OPTIONS" in text
    assert "--action probe" in text
    assert "48 h" in text
    for command in commands:
        argv = shlex.split(command)
        assert argv[0] in {"bq", "python"}
    alter = next(c for c in commands if "ALTER PROJECT" in c)
    sql = shlex.split(alter)[-1]
    assert sql == (
        "ALTER PROJECT `k1-project` "
        "SET OPTIONS (`region-europe-west2.default_max_time_travel_hours` = 48)"
    )


def test_live_commands_refuse_unsafe_names() -> None:
    with pytest.raises(spike.SpikeError):
        spike.live_commands("k1-project", "prod_data", "europe-west2", "marker")
    with pytest.raises(spike.SpikeError):
        spike.live_commands("k1-project; rm -rf /", "fa_spike_tt", "europe-west2", "marker")


def test_check_spike_dataset_requires_prefix() -> None:
    spike.check_spike_dataset("fa_spike_tt")
    for name in ("fa_analytics", "fa_spike_", "fa_spike_x y", "FA_SPIKE_X"):
        with pytest.raises(spike.SpikeError):
            spike.check_spike_dataset(name)


# run_bq: argv, no shell, timeout, error mapping.
class Done:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_run_bq_uses_argv_without_shell_and_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return Done("[]")

    monkeypatch.setattr(spike.subprocess, "run", fake_run)
    assert spike.run_bq(["bq", "ls"]) == "[]"
    assert seen["cmd"] == ["bq", "ls"]
    assert seen["kwargs"]["timeout"] == spike.BQ_TIMEOUT_S
    assert not seen["kwargs"].get("shell", False)
    assert seen["kwargs"]["check"] is True


def test_run_bq_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise FileNotFoundError("bq")

    monkeypatch.setattr(spike.subprocess, "run", fake_run)
    with pytest.raises(spike.SpikeError, match="not installed"):
        spike.run_bq(["bq", "ls"])


def test_run_bq_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(spike.subprocess, "run", fake_run)
    with pytest.raises(spike.SpikeError, match="timed out"):
        spike.run_bq(["bq", "ls"])


def test_run_bq_failure_becomes_query_error_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise subprocess.CalledProcessError(1, cmd, stderr=f"BigQuery error: {OUTSIDE_MSG}\n")

    monkeypatch.setattr(spike.subprocess, "run", fake_run)
    with pytest.raises(spike.QueryError, match="outside of time travel window"):
        spike.run_bq(["bq", "query"])


def test_run_bq_failure_with_no_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        raise subprocess.CalledProcessError(1, cmd, stderr=None)

    monkeypatch.setattr(spike.subprocess, "run", fake_run)
    with pytest.raises(spike.QueryError):
        spike.run_bq(["bq", "query"])


def test_bq_query_runner_builds_argv_and_parses_json() -> None:
    seen: list[list[str]] = []

    def runner(cmd: list[str]) -> str:
        seen.append(cmd)
        return '[{"n": "1"}]'

    run_query = spike.bq_query_runner("k1-project", "europe-west2", runner)
    assert run_query("SELECT 1") == [{"n": "1"}]
    assert seen == [
        [
            "bq",
            "--project_id=k1-project",
            "--location=europe-west2",
            "query",
            "--nouse_legacy_sql",
            "--format=json",
            "SELECT 1",
        ]
    ]


@pytest.mark.parametrize("raw", ["not json", '{"n": 1}', '["x"]', ""])
def test_bq_query_runner_rejects_non_row_output(raw: str) -> None:
    run_query = spike.bq_query_runner("k1-project", "us", lambda cmd: raw)
    with pytest.raises(spike.SpikeError):
        run_query("SELECT 1")


# CLI.
def live_args(*extra: str) -> list[str]:
    return [
        "--mode",
        "live",
        "--project",
        "k1-project",
        "--dataset",
        "fa_spike_tt",
        "--region",
        "europe-west2",
        *extra,
    ]


def test_live_commands_action_prints_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert spike.main(live_args()) == 0
    out = capsys.readouterr().out
    assert "ALTER PROJECT" in out
    assert "fa_spike_tt.marker" in out


def test_live_probe_prints_open(capsys: pytest.CaptureFixture[str]) -> None:
    code = spike.main(
        live_args("--action", "probe", "--ts", "2026-11-20T10:00:00Z"),
        runner=lambda cmd: '[{"n": "1"}]',
    )
    out = capsys.readouterr().out
    assert code == 0
    assert (
        out.strip() == "PROBE: k1-project.fa_spike_tt.marker at 2026-11-20T10:00:00+00:00 -> open"
    )


def test_live_probe_prints_closed_on_window_error(capsys: pytest.CaptureFixture[str]) -> None:
    def runner(cmd: list[str]) -> str:
        raise spike.QueryError(OUTSIDE_MSG)

    code = spike.main(live_args("--action", "probe", "--ts", "2026-11-17T10:00:00Z"), runner=runner)
    assert code == 0
    assert capsys.readouterr().out.strip().endswith("-> closed")


def test_live_probe_unknown_error_returns_1_and_prints_no_verdict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def runner(cmd: list[str]) -> str:
        raise spike.QueryError("Access Denied")

    code = spike.main(live_args("--action", "probe", "--ts", "2026-11-17T10:00:00Z"), runner=runner)
    assert code == 1
    assert "PROBE" not in capsys.readouterr().out


def test_live_probe_requires_ts(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        spike.main(live_args("--action", "probe"))
    assert exc.value.code == 2
    assert "--ts" in capsys.readouterr().err


def test_live_probe_rejects_naive_ts() -> None:
    code = spike.main(
        live_args("--action", "probe", "--ts", "2026-11-17T10:00:00"), runner=lambda c: "[]"
    )
    assert code == 1


def test_live_probe_rejects_unparseable_ts() -> None:
    code = spike.main(live_args("--action", "probe", "--ts", "yesterday"), runner=lambda c: "[]")
    assert code == 1


def test_live_schemata_ok_and_mismatch(capsys: pytest.CaptureFixture[str]) -> None:
    ok = spike.main(live_args("--action", "schemata"), runner=lambda c: '[{"option_value": "48"}]')
    assert ok == 0
    assert "max_time_travel_hours = 48 (expected 48: ok)" in capsys.readouterr().out
    bad = spike.main(
        live_args("--action", "schemata"), runner=lambda c: '[{"option_value": "168"}]'
    )
    assert bad == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_live_refuses_prod_looking_dataset() -> None:
    code = spike.main(
        ["--mode", "live", "--project", "p-project", "--dataset", "fa_analytics", "--region", "us"]
    )
    assert code == 1


@pytest.mark.parametrize("missing", ["--project", "--dataset", "--region"])
def test_live_needs_project_dataset_region(missing: str) -> None:
    args = live_args()
    index = args.index(missing)
    del args[index : index + 2]
    with pytest.raises(SystemExit) as exc:
        spike.main(args)
    assert exc.value.code == 2


# AC1/AC2 (demo): the two probes and the SCHEMATA_OPTIONS value in one report.
def test_demo_report_has_probes_hours_and_labelled_verdict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = spike.main([])
    out = capsys.readouterr().out
    assert code == 0
    report_text, _, verdict_line = out.strip().rpartition("\n")
    report = json.loads(report_text)
    assert report["demo"] is True
    assert report["table"] == TABLE
    assert report["probes"] == {"inside_window": "open", "older_than_window": "closed"}
    assert report["max_time_travel_hours"] == {"fa_spike_tt_inherit": 48}
    assert verdict_line.startswith(f"VERDICT: {spike.OBSERVABLE}")
    assert spike.UNVERIFIED in verdict_line
    assert "DEMO FIXTURE" in verdict_line


def test_demo_mismatch_returns_1(tmp_path: Path) -> None:
    for name in ("meta.json", "query_inside_window.json", "query_outside_window_error.json"):
        (tmp_path / name).write_text(
            (FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    schemata = load("schemata_options_inherit.json")
    schemata["rows"] = [{"f": [{"v": "168"}]}]
    (tmp_path / "schemata_options_inherit.json").write_text(json.dumps(schemata), encoding="utf-8")
    assert spike.main(["--fixtures-dir", str(tmp_path)]) == 1


def test_demo_meta_missing_field_returns_1(tmp_path: Path) -> None:
    meta = load("meta.json")
    del meta["table"]
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert spike.main(["--fixtures-dir", str(tmp_path)]) == 1


def test_demo_with_empty_fixtures_dir_returns_1(tmp_path: Path) -> None:
    assert spike.main(["--fixtures-dir", str(tmp_path)]) == 1


def test_documented_command_runs_as_module() -> None:
    backend = Path(__file__).resolve().parent.parent
    done = subprocess.run(
        [spike.sys.executable, "-m", "scripts.spike_bigquery_time_travel"],
        cwd=backend,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "DEMO FIXTURE" in done.stdout


# AC3: the ADR states the fail-safe is not observable and marks it `documented`.
def test_adr_states_fail_safe_not_observable_and_documented() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "not queryable" in text
    assert 'not "unrecoverable"' in text
    assert "unrecoverable" not in text.replace('not "unrecoverable"', "")
    assert "`documented`" in text
    assert "7 d fail-safe" in text
    assert "https://docs.cloud.google.com/bigquery/docs/time-travel" in text
    assert "https://docs.cloud.google.com/bigquery/docs/default-configuration" in text
    assert "2026-11-23" in text
    assert spike.UNVERIFIED in text
    assert "48 h" in text
