"""The Kafka broker answers an admin request (the same broker `docker compose` starts).

KAFKA_BOOTSTRAP is a test-only variable until Settings gains the Kafka keys (E5). The test skips
when the broker does not answer, and fails instead when REQUIRE_SERVICES=1.
"""

from __future__ import annotations

import os

import pytest
from confluent_kafka import KafkaException
from confluent_kafka.admin import AdminClient, ClusterMetadata

from tests.conftest import KAFKA_BOOTSTRAP, require_service

# Seconds the admin client waits for the broker (connect, socket and the request itself);
# test-only knob, overridable per environment.
ADMIN_TIMEOUT_S = float(os.environ.get("KAFKA_ADMIN_TIMEOUT_S", "10"))


def _admin_client(bootstrap: str) -> AdminClient:
    timeout_ms = int(ADMIN_TIMEOUT_S * 1000)
    return AdminClient(
        {
            "bootstrap.servers": bootstrap,
            "socket.timeout.ms": timeout_ms,
            "socket.connection.setup.timeout.ms": timeout_ms,
        }
    )


def _cluster_metadata(bootstrap: str) -> tuple[ClusterMetadata | None, str]:
    """Cluster metadata and an empty error, or no metadata and why the broker did not answer."""
    try:
        return _admin_client(bootstrap).list_topics(timeout=ADMIN_TIMEOUT_S), ""
    except KafkaException as exc:
        return None, str(exc)


@pytest.mark.integration
def test_broker_answers_a_list_topics_request() -> None:
    require_service("Kafka", bool(KAFKA_BOOTSTRAP), "set KAFKA_BOOTSTRAP to the broker host:port")
    metadata, error = _cluster_metadata(KAFKA_BOOTSTRAP)
    require_service("Kafka", metadata is not None, f"{error} at {KAFKA_BOOTSTRAP}")
    assert metadata is not None
    assert metadata.cluster_id, "the broker did not report a cluster id"
    assert metadata.brokers, "the cluster lists no broker"
    assert metadata.controller_id in metadata.brokers
