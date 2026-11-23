"""Unit tests for the unit/integration split and the REQUIRE_SERVICES gate (E0-T12).

Nothing here needs a running service: the subprocess tests point the database at a closed port.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import URL

from src.config import Settings
from tests import conftest
from tests.conftest import build_database_url, partition_items, require_service

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
TESTS_DIR = BACKEND_DIR / "tests"
SUBPROCESS_TIMEOUT_S = 120

# Every test that used `requires_db` before E0-T12 (30, counted by running the old suite without
# a database), per file. The Kafka test is new.
FORMER_REQUIRES_DB = {
    "test_lookup_factsheet.py": 6,
    "test_lookup_suitability.py": 5,
    "test_migrations.py": 5,
    "test_probes_integration.py": 2,
    "test_rls.py": 3,
    "test_rls_context.py": 2,
    "test_rls_e2e.py": 5,
    "test_search_kb.py": 2,
}
# Integration tests added since, per file: the Kafka reachability test (E0-T12) and the test
# helpers' tests (E0-T15: the Postgres race test, the two `kafka_stopped` tests).
ADDED_INTEGRATION = {
    "integration/test_kafka_reachable.py": 1,
    "helpers/test_race_harness.py": 1,
    "integration/test_kafka_stopped_fixture.py": 2,
}
EXPECTED_INTEGRATION_COUNT = sum(FORMER_REQUIRES_DB.values()) + sum(ADDED_INTEGRATION.values())

DB_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
FULL_ENV = {
    "DB_HOST": "db.example.invalid",
    "DB_PORT": "6543",
    "DB_NAME": "orders",
    "DB_USER": "svc",
    "DB_PASSWORD": "p@ss/word:1",
}


def _item(*marks: str) -> Any:
    names = set(marks)
    added: list[str] = []

    def get_closest_marker(name: str) -> object | None:
        return object() if name in names or name in added else None

    def add_marker(marker: Any) -> None:
        added.append(marker.name)
        names.add(marker.name)

    return SimpleNamespace(get_closest_marker=get_closest_marker, add_marker=add_marker)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _subprocess_env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DB_KEYS and k != "REQUIRE_SERVICES"}
    env.update(overrides)
    return env


def _pytest(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )


# build_database_url


def test_build_database_url_uses_the_settings_keys() -> None:
    url = build_database_url(FULL_ENV)
    assert isinstance(url, URL)
    assert url.drivername == "postgresql+asyncpg"
    assert (url.host, url.port, url.database, url.username) == (
        "db.example.invalid",
        6543,
        "orders",
        "svc",
    )
    assert url.password == "p@ss/word:1"


def test_build_database_url_port_defaults_to_the_settings_default() -> None:
    env = {k: v for k, v in FULL_ENV.items() if k != "DB_PORT"}
    url = build_database_url(env)
    assert url is not None
    assert url.port == Settings.model_fields["db_port"].default


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"])
def test_build_database_url_is_none_when_a_required_key_is_absent(missing: str) -> None:
    env = {k: v for k, v in FULL_ENV.items() if k != missing}
    assert build_database_url(env) is None


def test_build_database_url_is_none_for_a_blank_value() -> None:
    assert build_database_url({**FULL_ENV, "DB_HOST": "  "}) is None


def test_build_database_url_does_not_leak_the_password_when_rendered() -> None:
    url = build_database_url(FULL_ENV)
    assert url is not None
    assert "p@ss/word:1" not in repr(url)


# require_service


def test_require_service_returns_when_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "REQUIRE_SERVICES", True)
    require_service("PostgreSQL", True, "unused")


def test_require_service_skips_naming_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "REQUIRE_SERVICES", False)
    with pytest.raises(pytest.skip.Exception, match="PostgreSQL not reachable: refused"):
        require_service("PostgreSQL", False, "refused")


def test_require_service_fails_when_services_are_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conftest, "REQUIRE_SERVICES", True)
    with pytest.raises(pytest.fail.Exception, match="Kafka not reachable: refused"):
        require_service("Kafka", False, "refused")


# partition_items


def test_partition_items_marks_unmarked_tests_as_unit() -> None:
    plain, integration = _item(), _item("integration")
    selected, deselected = partition_items([plain, integration], run_live=False)
    assert selected == [plain, integration]
    assert deselected == []
    assert plain.get_closest_marker("unit") is not None
    assert integration.get_closest_marker("unit") is None


def test_partition_items_deselects_live_tests_by_default() -> None:
    plain, live = _item(), _item("live")
    selected, deselected = partition_items([plain, live], run_live=False)
    assert selected == [plain]
    assert deselected == [live]


def test_partition_items_keeps_live_tests_on_opt_in() -> None:
    live = _item("live")
    selected, deselected = partition_items([live], run_live=True)
    assert selected == [live]
    assert deselected == []


# Dependencies and markers


def test_greenlet_is_installed() -> None:
    """SQLAlchemy asyncio needs it; Poetry skipped it on Apple Silicon before it was declared."""
    import greenlet

    assert greenlet.__version__


def test_greenlet_and_confluent_kafka_are_pinned_dependencies() -> None:
    config = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text())["tool"]["poetry"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", config["dependencies"]["greenlet"])
    assert config["group"]["dev"]["dependencies"]["confluent-kafka"] == "2.16.1"


def test_unit_and_integration_markers_are_registered() -> None:
    markers = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text())["tool"]["pytest"][
        "ini_options"
    ]["markers"]
    names = {marker.split(":", 1)[0].strip() for marker in markers}
    assert {"unit", "integration", "live"} <= names


# AC4


def test_no_hardcoded_database_password_in_tests() -> None:
    needle = "local" + "dev"
    hits = [
        str(path.relative_to(REPO_ROOT))
        for path in TESTS_DIR.rglob("*.py")
        if needle in path.read_text()
    ]
    assert hits == []


# AC2, run for real against a closed port


def test_integration_tests_fail_when_services_are_required_and_the_db_is_down() -> None:
    env = _subprocess_env(
        **{**FULL_ENV, "DB_HOST": "127.0.0.1", "DB_PORT": str(_closed_port())},
        REQUIRE_SERVICES="1",
    )
    result = _pytest(["tests/test_rls_context.py", "-m", "integration", "-q", "--tb=line"], env)
    assert result.returncode != 0, result.stdout
    assert "2 failed" in result.stdout, result.stdout
    assert "skipped" not in result.stdout, result.stdout
    assert "PostgreSQL not reachable" in result.stdout


def test_integration_tests_skip_naming_postgres_when_services_are_not_required() -> None:
    env = _subprocess_env(
        **{**FULL_ENV, "DB_HOST": "127.0.0.1", "DB_PORT": str(_closed_port())},
    )
    result = _pytest(["tests/test_rls_context.py", "-m", "integration", "-q", "-rs"], env)
    assert result.returncode == 0, result.stdout
    assert "2 skipped" in result.stdout, result.stdout
    assert "PostgreSQL not reachable" in result.stdout


def test_integration_tests_skip_when_no_database_is_configured() -> None:
    result = _pytest(
        ["tests/test_rls_context.py", "-m", "integration", "-q", "-rs"], _subprocess_env()
    )
    assert result.returncode == 0, result.stdout
    assert "2 skipped" in result.stdout, result.stdout
    assert "DB_HOST" in result.stdout


# AC1 and AC5


def _collect(marker_expression: str, env: dict[str, str]) -> list[str]:
    result = _pytest(["--collect-only", "-m", marker_expression, "-q"], env)
    assert result.returncode == 0, result.stdout
    return [line for line in result.stdout.splitlines() if "::" in line]


def test_unit_selection_has_no_integration_and_no_live_tests() -> None:
    env = _subprocess_env(
        **{**FULL_ENV, "DB_HOST": "127.0.0.1", "DB_PORT": str(_closed_port())},
    )
    unit = _collect("not integration", env)
    integration = _collect("integration", env)
    assert unit
    assert set(unit).isdisjoint(integration)
    assert not any(i.startswith("tests/test_embeddings.py::") for i in unit), "live is deselected"
    assert not any(i.startswith("tests/integration/") for i in unit)


def test_integration_selection_lists_every_former_requires_db_test() -> None:
    ids = _collect("integration", _subprocess_env())
    assert len(ids) == EXPECTED_INTEGRATION_COUNT, ids
    for name, count in FORMER_REQUIRES_DB.items():
        assert sum(1 for i in ids if i.startswith(f"tests/{name}::")) == count, name
    for name, count in ADDED_INTEGRATION.items():
        assert sum(1 for i in ids if i.startswith(f"tests/{name}::")) == count, name


# AC3, dry run of the Makefile


def _make_dry_run(target: str, **env: str) -> str:
    """`make -n <target>` output; ENV_FILE defaults to .env.example unless a test passes one."""
    assert shutil.which("make"), "make is required to check the Makefile"
    base = {k: v for k, v in os.environ.items() if k not in {"ENV_FILE", "COMPOSE_WAIT_TIMEOUT_S"}}
    env = {"ENV_FILE": str(REPO_ROOT / ".env.example"), **env}
    result = subprocess.run(
        ["make", "-n", "-C", str(REPO_ROOT), target],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_make_deps_up_starts_postgres_kafka_and_migrate() -> None:
    assert "up -d --wait --wait-timeout 300 postgres kafka migrate" in _make_dry_run("deps-up")


def test_make_wait_timeout_is_a_variable() -> None:
    plan = _make_dry_run("deps-up", COMPOSE_WAIT_TIMEOUT_S="7")
    assert "--wait-timeout 7 postgres" in plan


def test_make_deps_down_removes_containers_and_volumes() -> None:
    assert "down -v" in _make_dry_run("deps-down")


def test_make_test_unit_deselects_integration() -> None:
    assert 'pytest -m "not integration"' in _make_dry_run("test-unit")


def test_make_test_integration_requires_services_and_always_tears_down() -> None:
    plan = _make_dry_run("test-integration")
    assert "REQUIRE_SERVICES=1" in plan
    assert "-m integration" in plan
    assert re.search(r"trap '.*down -v.*' EXIT", plan)
    assert "up -d --wait --wait-timeout 300 postgres kafka migrate" in plan


def test_make_test_integration_defaults_to_the_demo_env_file() -> None:
    assert _default_env_file() == str(REPO_ROOT / ".env.integration")


def _default_env_file() -> str:
    """The --env-file of the test-integration plan when ENV_FILE is not set."""
    base = {k: v for k, v in os.environ.items() if k != "ENV_FILE"}
    result = subprocess.run(
        ["make", "-n", "-C", str(REPO_ROOT), "test-integration"],
        env=base,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r"--env-file (\S+) up", result.stdout)
    assert match, result.stdout
    return match.group(1)


def test_make_test_integration_env_file_is_overridable_and_absolute() -> None:
    plan = _make_dry_run("test-integration", ENV_FILE="backend/../.env.example")
    env_files = set(re.findall(r"--env-file (\S+)", plan))
    assert env_files == {str(REPO_ROOT / ".env.example")}, "the trap must not use a relative path"


def test_make_other_targets_default_to_dot_env() -> None:
    base = {k: v for k, v in os.environ.items() if k != "ENV_FILE"}
    result = subprocess.run(
        ["make", "-n", "-C", str(REPO_ROOT), "deps-up"],
        env=base,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )
    assert result.returncode == 0, result.stderr
    assert f"--env-file {REPO_ROOT / '.env'} " in result.stdout


# The committed demo env file


def _read_env_file(path: Path) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    return {key: value for key, value in pairs if not key.startswith("#")}


def test_demo_env_file_boots_settings_without_real_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _read_env_file(REPO_ROOT / ".env.integration")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert len(settings.jwt_secret.get_secret_value()) >= 32
    assert values["DB_PORT"] == values["POSTGRES_HOST_PORT"]
    # The broker's in-network (19092) and controller (29093) listeners already use these ports.
    assert values["KAFKA_HOST_PORT"] not in {"19092", "29093"}
    assert not any("replace_me" in value.lower() for value in values.values())
    assert not any(value.startswith(("sk-", "pa-")) for value in values.values())
