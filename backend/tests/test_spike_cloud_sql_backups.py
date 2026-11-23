from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import spike_cloud_sql_backups as spike

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cloud_sql"
SEED_ID = "1790910007000"
NEW_ID = "1791342005000"


def run(backup_id: str, kind: str = "AUTOMATED") -> dict[str, Any]:
    return {
        "id": backup_id,
        "status": "SUCCESSFUL",
        "type": kind,
        "enqueuedTime": "2026-10-02T03:00:07.120Z",
        "instance": "fa-spike-backup",
    }


def body(*runs: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "sql#backupRunsList", "items": list(runs)}


def snaps(
    before_stop: list[dict[str, Any]],
    before_start: list[dict[str, Any]],
    after_start: list[dict[str, Any]],
    later: list[dict[str, Any]],
) -> spike.Snapshots:
    return spike.Snapshots(
        before_stop=spike.parse_backup_runs(body(*before_stop)),
        before_start=spike.parse_backup_runs(body(*before_start)),
        after_start=spike.parse_backup_runs(body(*after_start)),
        later=spike.parse_backup_runs(body(*later)),
    )


def verdict_for(s: spike.Snapshots, retention: int = 1, stopped: int = 4) -> spike.Verdict:
    return spike.compute_verdict(s, retention, stopped, verified=True)


# AC1: raw listings at the time points and the recorded retention.
def test_fixtures_parse() -> None:
    for label in spike.SNAPSHOT_LABELS:
        payload = json.loads((FIXTURES / f"backups_{label}.json").read_text(encoding="utf-8"))
        runs = spike.parse_backup_runs(payload)
        assert len(runs) == 1
        assert runs[0].type == "AUTOMATED"
        assert runs[0].instance == "fa-spike-backup"


def test_parse_accepts_gcloud_bare_list_and_ignores_extra_fields() -> None:
    runs = spike.parse_backup_runs([{**run("1"), "somethingNew": True}])
    assert [r.id for r in runs] == ["1"]


def test_parse_empty_listing() -> None:
    assert spike.parse_backup_runs({"kind": "sql#backupRunsList"}) == []


def test_parse_refuses_paged_body() -> None:
    with pytest.raises(spike.SpikeError, match="nextPageToken"):
        spike.parse_backup_runs({"items": [], "nextPageToken": "abc"})


def test_parse_refuses_bad_shape() -> None:
    with pytest.raises(spike.SpikeError, match="backupRuns.list"):
        spike.parse_backup_runs("not a listing")
    with pytest.raises(spike.SpikeError, match="BackupRun"):
        spike.parse_backup_runs({"items": [{"status": "SUCCESSFUL"}]})


def test_load_snapshots_reads_four_time_points_and_meta() -> None:
    snapshots, meta = spike.load_snapshots(FIXTURES)
    assert [r.id for r in snapshots.before_stop] == [SEED_ID]
    assert [r.id for r in snapshots.later] == [NEW_ID]
    assert meta["retained_backups_count"] == 1
    assert meta["stopped_days"] == 4


def test_load_snapshots_missing_file_is_a_spike_error(tmp_path: Path) -> None:
    with pytest.raises(spike.SpikeError, match="backups_before_stop.json"):
        spike.load_snapshots(tmp_path)


def test_load_snapshots_bad_json_is_a_spike_error(tmp_path: Path) -> None:
    for label in spike.SNAPSHOT_LABELS:
        (tmp_path / f"backups_{label}.json").write_text("{", encoding="utf-8")
    with pytest.raises(spike.SpikeError, match="not valid JSON"):
        spike.load_snapshots(tmp_path)


# AC2: one-line verdict checkable against the listings.
def test_verdict_expires_while_stopped() -> None:
    s = snaps([run("a")], [], [], [run("b")])
    result = verdict_for(s)
    assert result.verdict == spike.EXPIRES
    assert "absent from the before-start listing" in result.detail


def test_verdict_does_not_expire_still_present_at_24h() -> None:
    s = snaps([run("a")], [run("a")], [run("a")], [run("a")])
    result = verdict_for(s)
    assert result.verdict == spike.DOES_NOT_EXPIRE
    assert "still listed 24 h after start" in result.detail


def test_verdict_does_not_expire_pruned_after_start() -> None:
    s = snaps([run("a")], [run("a")], [run("a")], [run("b")])
    result = verdict_for(s)
    assert result.verdict == spike.DOES_NOT_EXPIRE
    assert "pruned between 'after start' and 24 h after start" in result.detail


def test_verdict_does_not_expire_pruned_on_start() -> None:
    s = snaps([run("a")], [run("a")], [], [run("b")])
    result = verdict_for(s)
    assert result.verdict == spike.DOES_NOT_EXPIRE
    assert "pruned between 'before start' and 'after start'" in result.detail


def test_verdict_inconclusive_when_stop_not_longer_than_retention() -> None:
    s = snaps([run("a")], [run("a")], [run("a")], [run("a")])
    result = verdict_for(s, retention=7, stopped=7)
    assert result.verdict == spike.INCONCLUSIVE
    assert "not longer than" in result.detail


def test_verdict_inconclusive_without_automated_seed() -> None:
    s = snaps([run("a", kind="ON_DEMAND")], [run("a", kind="ON_DEMAND")], [], [])
    result = verdict_for(s)
    assert result.verdict == spike.INCONCLUSIVE
    assert "AUTOMATED" in result.detail


def test_demo_fixture_verdict_is_unverified() -> None:
    snapshots, meta = spike.load_snapshots(FIXTURES)
    result = spike.compute_verdict(
        snapshots, meta["retained_backups_count"], meta["stopped_days"], verified=False
    )
    assert result.verdict == spike.DOES_NOT_EXPIRE
    assert result.verified is False
    assert spike.UNVERIFIED in result.line()
    assert "DEMO FIXTURE" in result.line()


def test_verified_verdict_line_has_no_unverified_label() -> None:
    result = verdict_for(snaps([run("a")], [], [], []))
    assert spike.UNVERIFIED not in result.line()
    assert result.line().startswith("VERDICT: expires while stopped")


# AC3: instance deleted at the end, no prod resource touched.
def test_live_commands_end_with_delete_and_list() -> None:
    cmds = spike.live_commands("k1-project", "fa-spike-backup", "us-central1", 1)
    assert cmds[-2].startswith("gcloud sql instances delete fa-spike-backup")
    assert cmds[-1] == "gcloud sql instances list --project=k1-project"
    joined = "\n".join(cmds)
    assert "--edition=ENTERPRISE" in joined
    assert "--retained-backups-count=1" in joined
    assert "--activation-policy=NEVER" in joined
    assert "--activation-policy=ALWAYS" in joined
    assert "backups create" not in joined


def test_live_commands_list_each_time_point() -> None:
    cmds = spike.live_commands("k1-project", "fa-spike-backup", "us-central1", 1)
    lists = [c for c in cmds if "sql backups list" in c]
    assert len(lists) == len(spike.SNAPSHOT_LABELS)


def test_live_refuses_non_spike_instance_name() -> None:
    with pytest.raises(spike.SpikeError, match="fa-spike-"):
        spike.check_spike_instance("finadvisor-prod")
    spike.check_spike_instance("fa-spike-backup")


def test_live_requires_project_and_instance(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        spike.main(["--mode", "live"])
    assert exc.value.code == 2
    assert "--project and --instance" in capsys.readouterr().err


def test_live_commands_action_requires_region(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        spike.main(["--mode", "live", "--project", "p", "--instance", "fa-spike-backup"])
    assert exc.value.code == 2
    assert "--region" in capsys.readouterr().err


def test_gcloud_backups_list_builds_exact_command() -> None:
    calls: list[list[str]] = []

    def fake(cmd: list[str]) -> str:
        calls.append(cmd)
        return "[]"

    assert spike.gcloud_backups_list("p", "fa-spike-backup", fake) == "[]"
    assert calls == [
        [
            "gcloud",
            "sql",
            "backups",
            "list",
            "--instance=fa-spike-backup",
            "--project=p",
            "--format=json",
        ]
    ]


def test_run_gcloud_success_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == spike.GCLOUD_TIMEOUT_S
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(spike.subprocess, "run", ok)
    assert spike.run_gcloud(["gcloud", "x"]) == "[]"

    def missing(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gcloud")

    monkeypatch.setattr(spike.subprocess, "run", missing)
    with pytest.raises(spike.SpikeError, match="not installed"):
        spike.run_gcloud(["gcloud", "x"])

    def slow(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(spike.subprocess, "run", slow)
    with pytest.raises(spike.SpikeError, match="timed out"):
        spike.run_gcloud(["gcloud", "x"])

    def failed(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd, stderr="PERMISSION_DENIED")

    monkeypatch.setattr(spike.subprocess, "run", failed)
    with pytest.raises(spike.SpikeError, match="PERMISSION_DENIED"):
        spike.run_gcloud(["gcloud", "x"])


# AC1 (live): the snapshot is the raw gcloud output, verbatim.
def test_snapshot_writes_raw_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = json.dumps([run("a")], indent=1)
    code = spike.main(
        [
            "--mode",
            "live",
            "--project",
            "p",
            "--instance",
            "fa-spike-backup",
            "--action",
            "snapshot",
            "--label",
            "before_stop",
            "--snapshots-dir",
            str(tmp_path),
        ],
        runner=lambda cmd: raw,
    )
    assert code == 0
    assert (tmp_path / "backups_before_stop.json").read_text(encoding="utf-8") == raw + "\n"
    assert "backups_before_stop.json" in capsys.readouterr().out


def test_snapshot_requires_label(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        spike.main(
            [
                "--mode",
                "live",
                "--project",
                "p",
                "--instance",
                "fa-spike-backup",
                "--action",
                "snapshot",
            ],
            runner=lambda cmd: "[]",
        )
    assert exc.value.code == 2
    assert "--label" in capsys.readouterr().err


def test_snapshot_refuses_unparseable_gcloud_output(tmp_path: Path) -> None:
    code = spike.main(
        [
            "--mode",
            "live",
            "--project",
            "p",
            "--instance",
            "fa-spike-backup",
            "--action",
            "snapshot",
            "--label",
            "after_start",
            "--snapshots-dir",
            str(tmp_path),
        ],
        runner=lambda cmd: "not json",
    )
    assert code == 1
    assert not (tmp_path / "backups_after_start.json").exists()


def test_live_commands_action_prints_commands(capsys: pytest.CaptureFixture[str]) -> None:
    code = spike.main(
        [
            "--mode",
            "live",
            "--project",
            "k1-project",
            "--instance",
            "fa-spike-backup",
            "--region",
            "us-central1",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "gcloud sql instances create fa-spike-backup" in out
    assert "--region=us-central1" in out


def test_live_refuses_prod_looking_instance() -> None:
    code = spike.main(
        ["--mode", "live", "--project", "p", "--instance", "finadvisor-prod", "--region", "r"]
    )
    assert code == 1


def test_live_evaluate_uses_snapshots_and_stopped_days(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for label in spike.SNAPSHOT_LABELS:
        source = (FIXTURES / f"backups_{label}.json").read_text(encoding="utf-8")
        (tmp_path / f"backups_{label}.json").write_text(source, encoding="utf-8")
    code = spike.main(
        [
            "--mode",
            "live",
            "--project",
            "p",
            "--instance",
            "fa-spike-backup",
            "--action",
            "evaluate",
            "--snapshots-dir",
            str(tmp_path),
            "--stopped-days",
            "4",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT: does not expire while stopped" in out
    assert spike.UNVERIFIED not in out


def test_live_evaluate_requires_stopped_days(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        spike.main(
            ["--mode", "live", "--project", "p", "--instance", "fa-spike-backup"]
            + ["--action", "evaluate"]
        )
    assert exc.value.code == 2
    assert "--stopped-days" in capsys.readouterr().err


def test_live_evaluate_missing_snapshots_returns_1(tmp_path: Path) -> None:
    code = spike.main(
        ["--mode", "live", "--project", "p", "--instance", "fa-spike-backup"]
        + ["--action", "evaluate", "--snapshots-dir", str(tmp_path), "--stopped-days", "3"]
    )
    assert code == 1


# AC1/AC2 (demo): the four listings, the retention and the labelled verdict in one report.
def test_demo_report_lists_time_points_and_retention(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = spike.main([])
    out = capsys.readouterr().out
    assert code == 0
    report_text, _, verdict_line = out.strip().rpartition("\n")
    report = json.loads(report_text)
    assert report["demo"] is True
    assert report["retained_backups_count"] == 1
    assert report["stopped_days"] == 4
    assert set(report["listings"]) == set(spike.SNAPSHOT_LABELS)
    assert report["listings"]["before_stop"] == [SEED_ID]
    assert report["listings"]["24h_after_start"] == [NEW_ID]
    assert verdict_line.startswith("VERDICT: does not expire while stopped")
    assert spike.UNVERIFIED in verdict_line
    assert "DEMO FIXTURE" in verdict_line


def test_demo_with_bad_snapshots_dir_returns_1(tmp_path: Path) -> None:
    assert spike.main(["--snapshots-dir", str(tmp_path)]) == 1


def test_documented_command_runs_as_module() -> None:
    backend = Path(__file__).resolve().parent.parent
    done = subprocess.run(
        [spike.sys.executable, "-m", "scripts.spike_cloud_sql_backups"],
        cwd=backend,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "DEMO FIXTURE" in done.stdout
