"""Fixtures for the integration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests.conftest import require_service
from tests.helpers.kafka_control import KafkaControlError, kafka_off, kafka_state

# `make test-integration` sets it: the broker is then the compose service this fixture can stop.
# Where Kafka is a plain service container (GitHub Actions) there is no compose project to stop.
KAFKA_COMPOSE_ENV = "KAFKA_COMPOSE"


@pytest.fixture
def kafka_stopped() -> Iterator[None]:
    """The compose Kafka broker is stopped for the test and started (healthy) at teardown.

    Skipped unless KAFKA_COMPOSE=1. Then the broker must be running when the test starts: without
    it the test is skipped, or failed with REQUIRE_SERVICES=1, naming the service."""
    if os.environ.get(KAFKA_COMPOSE_ENV) != "1":
        pytest.skip(f"{KAFKA_COMPOSE_ENV}=1 not set: Kafka is not a compose service here")
    try:
        state = kafka_state()
        reachable, detail = state.healthy, f"compose kafka is {state.state}"
    except KafkaControlError as exc:
        reachable, detail = False, str(exc)
    require_service("Kafka (docker compose)", reachable, detail)
    with kafka_off():
        yield
