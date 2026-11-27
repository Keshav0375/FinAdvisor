"""Stop and start the compose Kafka broker from a test (edge case #7: erasure with Kafka stopped).

The same commands as `make kafka-stop` and `make kafka-start`, run from the repository root with
an argv list (no shell). The compose project and env file come from the environment, as for any
`docker compose` call: `make test-integration` exports COMPOSE_PROJECT_NAME and COMPOSE_ENV_FILES.

Knobs: COMPOSE_WAIT_TIMEOUT_S (default 300, also the Makefile's) is how long a start waits for the
broker to be healthy; KAFKA_DOCKER_TIMEOUT_S (default 60) bounds the other docker calls. Both are
read at each call (`KafkaControlConfig.from_env`); a bad value fails naming the variable.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from tests.conftest import BACKEND_DIR
from tests.helpers.env import positive_number

REPO_ROOT = BACKEND_DIR.parent
SERVICE = "kafka"
WAIT_TIMEOUT_ENV = "COMPOSE_WAIT_TIMEOUT_S"
DOCKER_TIMEOUT_ENV = "KAFKA_DOCKER_TIMEOUT_S"


@dataclass(frozen=True)
class KafkaControlConfig:
    wait_timeout_s: int
    docker_timeout_s: float

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> KafkaControlConfig:
        return cls(
            wait_timeout_s=positive_number(environ, WAIT_TIMEOUT_ENV, 300, int),
            docker_timeout_s=positive_number(environ, DOCKER_TIMEOUT_ENV, 60.0, float),
        )


class KafkaControlError(Exception):
    """A docker compose call failed, or the broker is not in the state the call should leave."""


@dataclass(frozen=True)
class KafkaState:
    """`state` is compose's container state (running, exited, ...) or "absent" with no container;
    `health` is its healthcheck status (healthy, starting, ...) or empty."""

    state: str
    health: str

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def healthy(self) -> bool:
        return self.running and self.health == "healthy"


def compose(*args: str, timeout_s: float | None = None) -> str:
    """Run `docker compose <args>` from the repository root; `timeout_s` defaults to
    KAFKA_DOCKER_TIMEOUT_S."""
    if timeout_s is None:
        timeout_s = KafkaControlConfig.from_env(os.environ).docker_timeout_s
    argv = ["docker", "compose", *args]
    try:
        result = subprocess.run(
            argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KafkaControlError(f"{' '.join(argv)}: {exc!r}") from exc
    if result.returncode != 0:
        raise KafkaControlError(
            f"{' '.join(argv)} exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def parse_ps(output: str) -> KafkaState:
    """The state in `docker compose ps --all --format json kafka` (one JSON object per line, or one
    JSON array, depending on the compose version)."""
    text = output.strip()
    if not text:
        return KafkaState("absent", "")
    try:
        parsed: Any = json.loads(text)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        try:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise KafkaControlError(f"cannot read compose ps output: {exc}") from exc
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise KafkaControlError(f"expected one {SERVICE} container, compose ps listed {len(rows)}")
    return KafkaState(str(rows[0].get("State", "")), str(rows[0].get("Health", "")))


def kafka_state() -> KafkaState:
    return parse_ps(compose("ps", "--all", "--format", "json", SERVICE))


def stop_kafka() -> None:
    """`docker compose stop kafka`; fails unless the broker is then not running."""
    compose("stop", SERVICE)
    state = kafka_state()
    if state.running:
        raise KafkaControlError(f"{SERVICE} is still running after stop: {state}")


def start_kafka() -> None:
    """Start the broker and wait for its healthcheck; fails unless it is then healthy."""
    config = KafkaControlConfig.from_env(os.environ)
    compose(
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        str(config.wait_timeout_s),
        SERVICE,
        timeout_s=config.wait_timeout_s + config.docker_timeout_s,
    )
    state = kafka_state()
    if not state.healthy:
        raise KafkaControlError(f"{SERVICE} is not healthy after start: {state}")


@contextmanager
def kafka_off() -> Iterator[None]:
    """The broker is stopped inside the block and started and healthy after it, also on failure."""
    try:
        stop_kafka()
        yield
    finally:
        start_kafka()
