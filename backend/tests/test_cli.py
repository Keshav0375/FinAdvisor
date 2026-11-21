from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

import pytest

from src import cli


def _fake_run(
    returncode: int, calls: list[tuple[list[str], str]]
) -> Callable[[list[str], str], subprocess.CompletedProcess[str]]:
    def run(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        calls.append((args, cwd))
        return subprocess.CompletedProcess(args, returncode)

    return run


def test_ensure_schema_runs_the_migrate_job_from_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_run(0, calls))
    cli.ensure_schema()
    assert calls == [
        ([sys.executable, "-m", "src.entrypoint", "jobs", "migrate"], str(cli.BACKEND_DIR))
    ]


def test_ensure_schema_exits_1_when_migrate_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.subprocess, "run", _fake_run(1, []))
    with pytest.raises(SystemExit) as raised:
        cli.ensure_schema()
    assert raised.value.code == 1
    assert "migration failed" in capsys.readouterr().err
