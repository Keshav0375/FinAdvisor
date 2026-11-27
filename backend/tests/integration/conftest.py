"""Fixtures and collection rules for the integration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence

import pytest

from tests.conftest import require_service
from tests.helpers.kafka_control import KafkaControlError, kafka_off, kafka_state

# `make test-integration` sets it: the broker is then the compose service the `kafka_stopped`
# fixture can stop. Where Kafka is a plain service container (GitHub Actions) there is no compose
# project to stop, and the tests marked `compose_only` are deselected (never skipped, so a run with
# `check_no_skips` stays green).
KAFKA_COMPOSE_ENV = "KAFKA_COMPOSE"
COMPOSE_ONLY_MARKER = "compose_only"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{COMPOSE_ONLY_MARKER}: needs the docker compose stack (KAFKA_COMPOSE=1); "
        "deselected otherwise",
    )


def split_compose_only(
    items: Sequence[pytest.Item], compose: bool
) -> tuple[list[pytest.Item], list[pytest.Item]]:
    """(selected, deselected): `compose_only` tests are deselected unless `compose` is True."""
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if not compose and item.get_closest_marker(COMPOSE_ONLY_MARKER) is not None:
            deselected.append(item)
        else:
            selected.append(item)
    return selected, deselected


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    selected, deselected = split_compose_only(items, os.environ.get(KAFKA_COMPOSE_ENV) == "1")
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


@pytest.fixture
def kafka_stopped() -> Iterator[None]:
    """The compose Kafka broker is stopped for the test and started (healthy) at teardown.

    Mark the test `compose_only`. The broker must run when the test starts: without it the test
    is skipped, or failed with REQUIRE_SERVICES=1, naming the service."""
    try:
        state = kafka_state()
        reachable, detail = state.healthy, f"compose kafka is {state.state}"
    except KafkaControlError as exc:
        reachable, detail = False, str(exc)
    require_service("Kafka (docker compose)", reachable, detail)
    with kafka_off():
        yield
