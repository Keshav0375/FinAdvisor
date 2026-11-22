"""The Kafka broker answers an admin request (the same broker `docker compose` starts).

KAFKA_BOOTSTRAP is a test-only variable until Settings gains the Kafka keys (E5). The test skips
when the broker does not answer, and fails instead when REQUIRE_SERVICES=1.
"""

from __future__ import annotations

import os

import pytest
from confluent_kafka import KafkaException
from confluent_kafka.admin import AdminClient

from tests.conftest import KAFKA_BOOTSTRAP, require_service

# Seconds the admin client waits for the broker; test-only knob, overridable per environment.
ADMIN_TIMEOUT_S = float(os.environ.get("KAFKA_ADMIN_TIMEOUT_S", "10"))


def _list_topics(bootstrap: str) -> tuple[list[str], str]:
    """Topic names and an empty error, or no topics and why the broker did not answer."""
    client = AdminClient({"bootstrap.servers": bootstrap, "socket.timeout.ms": 5000})
    try:
        metadata = client.list_topics(timeout=ADMIN_TIMEOUT_S)
    except KafkaException as exc:
        return [], str(exc)
    return sorted(metadata.topics), ""


@pytest.mark.integration
def test_broker_answers_a_list_topics_request() -> None:
    require_service("Kafka", bool(KAFKA_BOOTSTRAP), "set KAFKA_BOOTSTRAP to the broker host:port")
    topics, error = _list_topics(KAFKA_BOOTSTRAP)
    require_service("Kafka", not error, f"{error} at {KAFKA_BOOTSTRAP}")
    assert isinstance(topics, list)
