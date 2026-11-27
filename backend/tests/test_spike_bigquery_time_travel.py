from __future__ import annotations

import json
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import spike_bigquery_time_travel as spike
from src.lifecycle import bq_probe

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bigquery"
ADR = Path(__file__).resolve().parents[2] / "docs" / "adr" / "bigquery-erasure-probe.md"
TABLE = "fa-spike-demo.fa_spike_tt.marker"
TS = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
OUTSIDE_MSG = "Invalid snapshot time 1763373600000 for table p:d.t, outside of time travel window."


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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


# AC1/AC2 (demo): the fixture runner feeds the real probe.
def test_fixture_runner_serves_inside_outside_and_schemata() -> None:
    run_query = spike.fixture_runner(FIXTURES, load("meta.json"))
    assert bq_probe.bq_time_travel_probe(TABLE, TS, run_query=run_query) == "open"
    old = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)
    assert bq_probe.bq_time_travel_probe(TABLE, old, run_query=run_query) == "closed"
    hours = bq_probe.read_time_travel_hours(
        "fa-spike-demo", "europe-west2", "fa_spike_tt_inherit", run_query=run_query
    )
    assert hours == 48


def test_fixture_runner_has_no_answer_for_unknown_timestamp() -> None:
    run_query = spike.fixture_runner(FIXTURES, load("meta.json"))
    with pytest.raises(spike.SpikeError, match="no fixture"):
        bq_probe.bq_time_travel_probe(TABLE, datetime(2020, 1, 1, tzinfo=UTC), run_query=run_query)


def test_fixture_runner_missing_file_raises(tmp_path: Path) -> None:
    run_query = spike.fixture_runner(tmp_path, load("meta.json"))
    with pytest.raises(spike.SpikeError, match="cannot read"):
        bq_probe.bq_time_travel_probe(TABLE, TS, run_query=run_query)


def test_fixture_runner_bad_json_raises(tmp_path: Path) -> None:
    (tmp_path / "query_inside_window.json").write_text("not json", encoding="utf-8")
    run_query = spike.fixture_runner(tmp_path, load("meta.json"))
    with pytest.raises(spike.SpikeError, match="not valid JSON"):
        bq_probe.bq_time_travel_probe(TABLE, TS, run_query=run_query)


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
        ("open_empty", "closed", 48, "returned open_empty"),
        ("open", "open", 48, "older than the window"),
        ("open", "open_empty", 48, "returned open_empty"),
        ("open", "closed", 72, "inherit"),
    ],
)
def test_verdict_mismatch_names_the_failed_check(
    inside: bq_probe.ProbeResult, outside: bq_probe.ProbeResult, hours: int, needle: str
) -> None:
    result = spike.compute_verdict(inside, outside, hours, verified=True)
    assert result.verdict == spike.MISMATCH
    assert needle in result.detail


# Config: the same env keys as Settings, validated with the field's own constraints.
def test_configured_defaults_without_env() -> None:
    assert spike.configured("bq_bin", "BQ_BIN", str, {}) == "bq"
    assert spike.configured("bq_timeout_s", "BQ_TIMEOUT_S", float, {}) == 120.0


def test_configured_reads_env_overrides() -> None:
    assert spike.configured("bq_bin", "BQ_BIN", str, {"BQ_BIN": "/x/bq"}) == "/x/bq"
    assert spike.configured("bq_timeout_s", "BQ_TIMEOUT_S", float, {"BQ_TIMEOUT_S": "9"}) == 9.0


@pytest.mark.parametrize(("name", "bad"), [("BQ_TIMEOUT_S", "0"), ("BQ_TIMEOUT_S", "x")])
def test_configured_rejects_bad_timeout(name: str, bad: str) -> None:
    with pytest.raises(spike.SpikeError, match=name):
        spike.configured("bq_timeout_s", name, float, {name: bad})


def test_configured_rejects_blank_binary() -> None:
    with pytest.raises(spike.SpikeError, match="BQ_BIN"):
        spike.configured("bq_bin", "BQ_BIN", str, {"BQ_BIN": ""})


# Printed commands.
def test_live_commands_are_shlex_quoted_and_cover_the_flow() -> None:
    lines = spike.live_commands("k1-project", "fa_spike_tt", "europe-west2", "marker", "bq")
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
        assert shlex.split(command)[0] in {"bq", "python"}
    alter = next(c for c in commands if "ALTER PROJECT" in c)
    assert shlex.split(alter)[-1] == (
        "ALTER PROJECT `k1-project` "
        "SET OPTIONS (`region-europe-west2.default_max_time_travel_hours` = 48)"
    )


def test_live_commands_use_the_configured_binary() -> None:
    lines = spike.live_commands("k1-project", "fa_spike_tt", "us", "marker", "/opt/bq")
    bq_lines = [x for x in lines if not x.startswith(("#", "python"))]
    assert bq_lines
    assert all(shlex.split(line)[0] == "/opt/bq" for line in bq_lines)


def test_live_commands_refuse_unsafe_names() -> None:
    with pytest.raises(spike.SpikeError):
        spike.live_commands("k1-project", "prod_data", "europe-west2", "marker", "bq")
    with pytest.raises(bq_probe.BqProbeError):
        spike.live_commands("k1-project; rm -rf /", "fa_spike_tt", "europe-west2", "marker", "bq")


def test_check_spike_dataset_requires_prefix() -> None:
    spike.check_spike_dataset("fa_spike_tt")
    for name in ("fa_analytics", "fa_spike_", "fa_spike_x y", "FA_SPIKE_X"):
        with pytest.raises(spike.SpikeError):
            spike.check_spike_dataset(name)


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


def test_live_commands_action_honours_bq_bin_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BQ_BIN", "/opt/bq")
    assert spike.main(live_args()) == 0
    assert "/opt/bq --project_id=k1-project" in capsys.readouterr().out


def test_live_bad_timeout_env_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BQ_TIMEOUT_S", "-3")
    assert spike.main(live_args()) == 1


def test_live_probe_uses_env_binary_and_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BQ_BIN", "/opt/bq")
    monkeypatch.setenv("BQ_TIMEOUT_S", "42")
    seen: dict[str, Any] = {}

    class Done:
        stdout = '[{"n": "1"}]'

    def fake_run(cmd: list[str], **kwargs: Any) -> Done:
        seen["cmd"] = cmd
        seen["timeout"] = kwargs["timeout"]
        return Done()

    monkeypatch.setattr(bq_probe.subprocess, "run", fake_run)
    code = spike.main(live_args("--action", "probe", "--ts", "2026-11-20T10:00:00Z"))
    assert code == 0
    assert seen["cmd"][0] == "/opt/bq"
    assert seen["timeout"] == 42.0
    assert capsys.readouterr().out.strip().endswith("-> open")


def test_live_probe_prints_open(capsys: pytest.CaptureFixture[str]) -> None:
    code = spike.main(
        live_args("--action", "probe", "--ts", "2026-11-20T10:00:00Z"),
        runner=lambda cmd: '[{"n": "1"}]',
    )
    out = capsys.readouterr().out
    assert code == 0
    expected = "PROBE: k1-project.fa_spike_tt.marker at 2026-11-20T10:00:00+00:00 -> open"
    assert out.strip() == expected


def test_live_probe_prints_open_empty_not_closed(capsys: pytest.CaptureFixture[str]) -> None:
    code = spike.main(
        live_args("--action", "probe", "--ts", "2026-11-20T10:00:00Z"),
        runner=lambda cmd: '[{"n": "0"}]',
    )
    assert code == 0
    assert capsys.readouterr().out.strip().endswith("-> open_empty")


def test_live_probe_prints_closed_on_window_error(capsys: pytest.CaptureFixture[str]) -> None:
    def runner(cmd: list[str]) -> str:
        raise bq_probe.QueryError(OUTSIDE_MSG)

    code = spike.main(live_args("--action", "probe", "--ts", "2026-11-17T10:00:00Z"), runner=runner)
    assert code == 0
    assert capsys.readouterr().out.strip().endswith("-> closed")


def test_live_probe_unknown_error_returns_1_and_prints_no_verdict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def runner(cmd: list[str]) -> str:
        raise bq_probe.QueryError("Access Denied")

    code = spike.main(live_args("--action", "probe", "--ts", "2026-11-17T10:00:00Z"), runner=runner)
    assert code == 1
    assert "PROBE" not in capsys.readouterr().out


def test_live_probe_requires_ts(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        spike.main(live_args("--action", "probe"))
    assert exc.value.code == 2
    assert "--ts" in capsys.readouterr().err


@pytest.mark.parametrize("ts", ["2026-11-17T10:00:00", "yesterday"])
def test_live_probe_rejects_naive_or_unparseable_ts(ts: str) -> None:
    code = spike.main(live_args("--action", "probe", "--ts", ts), runner=lambda c: "[]")
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


def copy_fixtures(target: Path) -> None:
    for name in ("meta.json", "query_inside_window.json", "query_outside_window_error.json"):
        (target / name).write_text((FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8")


def test_demo_mismatch_returns_1(tmp_path: Path) -> None:
    copy_fixtures(tmp_path)
    schemata = load("schemata_options_inherit.json")
    schemata["rows"] = [{"f": [{"v": "168"}]}]
    (tmp_path / "schemata_options_inherit.json").write_text(json.dumps(schemata), encoding="utf-8")
    assert spike.main(["--fixtures-dir", str(tmp_path)]) == 1


def test_demo_inside_probe_with_no_rows_is_a_mismatch_not_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy_fixtures(tmp_path)
    empty = load("query_inside_window.json")
    empty["rows"] = [{"f": [{"v": "0"}]}]
    (tmp_path / "query_inside_window.json").write_text(json.dumps(empty), encoding="utf-8")
    (tmp_path / "schemata_options_inherit.json").write_text(
        (FIXTURES / "schemata_options_inherit.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert spike.main(["--fixtures-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert '"inside_window": "open_empty"' in out
    assert "VERDICT: mismatch" in out


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


def test_adr_names_open_empty_and_the_module_path() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "open_empty" in text
    assert "backend/src/lifecycle/bq_probe.py" in text
    assert "BQ_TIMEOUT_S" in text
