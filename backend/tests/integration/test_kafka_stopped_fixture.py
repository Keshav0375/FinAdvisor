"""`kafka_stopped` stops the compose broker for a test and leaves it healthy afterwards.

Needs the compose stack of `make test-integration` (KAFKA_COMPOSE=1); skipped elsewhere, or failed
with REQUIRE_SERVICES=1 when the compose broker is down.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.conftest import require_service
from tests.helpers.kafka_control import compose, kafka_off, kafka_state

pytestmark = pytest.mark.integration


def test_broker_is_not_running_inside_the_fixture(kafka_stopped: None) -> None:
    listed = compose("ps", "--format", "json", "kafka")
    running = [row for row in _json_rows(listed) if row.get("State") == "running"]
    assert running == [], "docker compose ps still lists a running kafka"
    assert not kafka_state().running


def test_broker_is_healthy_again_after_the_block() -> None:
    if os.environ.get("KAFKA_COMPOSE") != "1":
        pytest.skip("KAFKA_COMPOSE=1 not set: Kafka is not a compose service here")
    before = kafka_state()
    require_service("Kafka (docker compose)", before.healthy, f"compose kafka is {before.state}")
    with kafka_off():
        assert not kafka_state().running
    after = kafka_state()
    assert after.healthy, after


def _json_rows(output: str) -> list[dict[str, str]]:
    text = output.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines()]
