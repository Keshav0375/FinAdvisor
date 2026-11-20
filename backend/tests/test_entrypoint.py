from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError

from src import entrypoint
from src.config import Settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
MIGRATE_TIMEOUT_S = 30


def test_bogus_command_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        entrypoint.main(["bogus"])
    assert raised.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["jobs"], ["jobs", "bogus"], ["api", "extra"]])
def test_incomplete_or_unknown_commands_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        entrypoint.main(argv)
    assert raised.value.code == 2


def test_api_serves_on_all_interfaces_with_one_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    assert entrypoint.main(["api"]) == 0
    assert calls == [(("src.main:app",), {"host": "0.0.0.0", "port": 8000, "workers": 1})]


def test_api_port_comes_from_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8123")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    assert entrypoint.main(["api"]) == 0
    assert calls[0]["port"] == 8123


def test_invalid_config_exits_nonzero_without_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DB_HOST")
    ran: list[str] = []
    monkeypatch.setattr(command, "upgrade", lambda *a, **kw: ran.append("upgrade"))
    assert entrypoint.main(["jobs", "migrate"]) == 1
    assert ran == []
    assert "DB_HOST" in capsys.readouterr().out


def test_migrate_upgrades_to_head_with_the_repo_alembic_ini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_upgrade(config: Config, revision: str) -> None:
        calls.append((config.config_file_name or "", revision))

    monkeypatch.setattr(command, "upgrade", fake_upgrade)
    assert entrypoint.main(["jobs", "migrate"]) == 0
    assert calls == [(str(BACKEND_DIR / "alembic.ini"), "head")]


def test_migrate_failure_is_nonzero_and_hides_the_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    password = Settings().db_password.get_secret_value()

    def failing_upgrade(config: Config, revision: str) -> None:
        raise OperationalError("connect", {}, Exception(f"postgresql://u:{password}@h/db refused"))

    monkeypatch.setattr(command, "upgrade", failing_upgrade)
    assert entrypoint.main(["jobs", "migrate"]) == 1
    out = capsys.readouterr().out
    assert "migrate_failed" in out
    assert password not in out


def test_redact_removes_raw_and_url_quoted_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word")
    settings = Settings()
    redacted = entrypoint.redact("raw p@ss/word quoted p%40ss%2Fword", settings)
    assert "p@ss/word" not in redacted
    assert "p%40ss%2Fword" not in redacted


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_migrate_against_closed_port_fails_fast_without_leaking_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "sentinel-db-password-9f3a"
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", str(_closed_port()))
    monkeypatch.setenv("DB_PASSWORD", password)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "src.entrypoint", "jobs", "migrate"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=MIGRATE_TIMEOUT_S,
    )
    elapsed = time.monotonic() - started
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert elapsed < MIGRATE_TIMEOUT_S
    assert "migrate_failed" in output
    assert password not in output
