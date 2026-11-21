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
    assert calls == [[*COMPOSE, "up", "-d", "--wait", "postgres"]]


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
        [*COMPOSE, "up", "--no-deps", "--exit-code-from", "migrate", "migrate"],
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
