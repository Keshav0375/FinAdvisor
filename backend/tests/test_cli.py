from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from src import cli

ENV_VARS = {"DB_USER": "fa_user", "DB_NAME": "fa_db"}
ENV_FILE = str(cli.BACKEND_DIR / ".env")
COMPOSE = ["docker", "compose", "--env-file", ENV_FILE]


def _fake_compose(
    returncode: int, calls: list[list[str]], stdout: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout)

    return run


def test_docker_compose_uses_the_backend_env_file_from_the_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli._docker_compose("ps", capture=True)
    assert seen["cmd"] == [*COMPOSE, "ps"]
    assert seen["kwargs"] == {"cwd": str(cli.PROJECT_ROOT), "capture_output": True, "text": True}


def test_start_postgres_waits_for_the_healthcheck(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(0, calls))
    cli.start_postgres()
    assert calls == [[*COMPOSE, "up", "-d", "--wait", "--wait-timeout", "300", "postgres"]]


def test_start_postgres_wait_timeout_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(0, calls))
    monkeypatch.setenv("COMPOSE_WAIT_TIMEOUT_S", "45")
    cli.start_postgres()
    assert calls[0][-3:-1] == ["--wait-timeout", "45"]


@pytest.mark.parametrize("bad", ["0", "-3", "soon", "", "\u00b2"])
def test_a_bad_wait_timeout_exits_1_before_docker_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(0, calls))
    monkeypatch.setenv("COMPOSE_WAIT_TIMEOUT_S", bad)
    with pytest.raises(SystemExit) as raised:
        cli.start_postgres()
    assert raised.value.code == 1
    assert calls == []
    assert "COMPOSE_WAIT_TIMEOUT_S" in capsys.readouterr().err


def test_start_postgres_exits_1_when_it_never_becomes_healthy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(1, []))
    with pytest.raises(SystemExit) as raised:
        cli.start_postgres()
    assert raised.value.code == 1
    assert "did not become healthy" in capsys.readouterr().err


def test_ensure_schema_runs_the_migrate_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(0, calls))
    cli.ensure_schema()
    assert calls == [
        [*COMPOSE, "up", "--build", "--no-deps", "--exit-code-from", "migrate", "migrate"],
    ]


def test_ensure_schema_exits_1_when_migrate_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(1, []))
    with pytest.raises(SystemExit) as raised:
        cli.ensure_schema()
    assert raised.value.code == 1
    assert "migration failed" in capsys.readouterr().err


def test_psql_query_uses_the_configured_role_and_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(0, calls, stdout=" 7\n"))
    assert cli._psql_query("SELECT 1", ENV_VARS) == (0, "7")
    assert calls == [
        [
            *COMPOSE,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fa_user",
            "-d",
            "fa_db",
            "-tAc",
            "SELECT 1",
        ]
    ]


def test_ensure_data_ingested_skips_when_chunks_exist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(0, calls, stdout="12"))
    cli.ensure_data_ingested(ENV_VARS)
    assert len(calls) == 1
    assert "skipping" in capsys.readouterr().out


def test_ensure_data_ingested_fails_without_a_corpus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.subprocess, "run", _fake_compose(0, [], stdout="0"))
    monkeypatch.setattr(cli, "CORPUS_DIR", tmp_path)
    with pytest.raises(SystemExit) as raised:
        cli.ensure_data_ingested(ENV_VARS)
    assert raised.value.code == 1
    assert "No corpus files" in capsys.readouterr().err


def test_ensure_data_ingested_runs_the_ingest_script_when_the_corpus_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "doc.json").write_text("{}")
    compose_calls: list[list[str]] = []
    monkeypatch.setattr(cli, "_docker_compose", lambda *a, **k: _result(0, "0", compose_calls, a))
    monkeypatch.setattr(cli, "CORPUS_DIR", tmp_path)
    ingest_calls: list[list[str]] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        ingest_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli.ensure_data_ingested(ENV_VARS)
    assert ingest_calls == [[cli.sys.executable, "scripts/ingest.py"]]


def test_ensure_data_ingested_exits_1_when_ingest_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "doc.json").write_text("{}")
    monkeypatch.setattr(cli, "_docker_compose", lambda *a, **k: _result(0, "", [], a))
    monkeypatch.setattr(cli, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3)
    )
    with pytest.raises(SystemExit) as raised:
        cli.ensure_data_ingested(ENV_VARS)
    assert raised.value.code == 1
    assert "Ingest failed" in capsys.readouterr().err


def _result(
    returncode: int, stdout: str, calls: list[list[str]], args: tuple[str, ...]
) -> subprocess.CompletedProcess[str]:
    calls.append(list(args))
    return subprocess.CompletedProcess(list(args), returncode, stdout=stdout)


def test_check_env_reads_backend_env_and_reports_missing_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "BACKEND_DIR", tmp_path)
    with pytest.raises(SystemExit):
        cli.check_env()
    assert ".env not found" in capsys.readouterr().err

    (tmp_path / ".env").write_text('# c\nDB_USER="u"\nDB_NAME=n\n')
    with pytest.raises(SystemExit):
        cli.check_env()
    assert "DB_HOST" in capsys.readouterr().err

    full = "\n".join(f"{key}=v" for key in cli.REQUIRED_ENV_KEYS)
    (tmp_path / ".env").write_text(full)
    assert cli.check_env()["DB_HOST"] == "v"


def test_main_starts_compose_postgres_then_migrates_then_ingests_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class Proc:
        def __init__(self, name: str) -> None:
            self.name = name

        def wait(self, timeout: float | None = None) -> int:
            if self.name == "backend" and timeout is None:
                raise KeyboardInterrupt
            return 0

        def terminate(self) -> None:
            order.append(f"terminate {self.name}")

        def kill(self) -> None:
            order.append(f"kill {self.name}")

    steps = {
        "check_docker": None,
        "free_ports": None,
        "start_postgres": None,
        "ensure_schema": None,
        "wait_for_backend": None,
    }
    for name in steps:
        monkeypatch.setattr(cli, name, lambda n=name: order.append(n))
    monkeypatch.setattr(cli, "check_env", lambda: ENV_VARS)
    monkeypatch.setattr(cli, "ensure_data_ingested", lambda env: order.append("ingest"))
    monkeypatch.setattr(cli, "start_backend", lambda env: Proc("backend"))
    monkeypatch.setattr(cli, "start_frontend", lambda: Proc("frontend"))
    cli.main()
    assert order[:5] == [
        "check_docker",
        "free_ports",
        "start_postgres",
        "ensure_schema",
        "ingest",
    ]
    assert order[-2:] == ["terminate frontend", "terminate backend"]
