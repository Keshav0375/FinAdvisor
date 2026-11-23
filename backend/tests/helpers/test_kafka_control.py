"""The docker compose calls behind `kafka_stopped`, with `subprocess.run` replaced (no docker)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

import pytest

from tests.helpers import kafka_control
from tests.helpers.kafka_control import (
    REPO_ROOT,
    KafkaControlError,
    KafkaState,
    kafka_off,
    kafka_state,
    parse_ps,
    start_kafka,
    stop_kafka,
)

RUNNING = json.dumps({"Service": "kafka", "State": "running", "Health": "healthy"})
STARTING = json.dumps({"Service": "kafka", "State": "running", "Health": "starting"})
EXITED = json.dumps({"Service": "kafka", "State": "exited", "Health": ""})


class FakeDocker:
    """Answers `docker compose` calls; `ps_outputs` are the successive `ps` answers."""

    def __init__(self, ps_outputs: list[str], fail_on: str | None = None) -> None:
        self.ps_outputs = list(ps_outputs)
        self.fail_on = fail_on
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        assert kwargs["cwd"] == REPO_ROOT
        verb = argv[2]
        if verb == self.fail_on:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        out = self.ps_outputs.pop(0) if verb == "ps" else ""
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeDocker]:
    def install(ps_outputs: list[str], fail_on: str | None = None) -> FakeDocker:
        fake = FakeDocker(ps_outputs, fail_on)
        monkeypatch.setattr(kafka_control.subprocess, "run", fake)
        return fake

    return install


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", KafkaState("absent", "")),
        (RUNNING, KafkaState("running", "healthy")),
        (f"[{EXITED}]", KafkaState("exited", "")),
        (f"{STARTING}\n", KafkaState("running", "starting")),
    ],
)
def test_parse_ps(output: str, expected: KafkaState) -> None:
    assert parse_ps(output) == expected


@pytest.mark.parametrize("output", ["{not json", f"{RUNNING}\n{EXITED}", "[]", "[1]"])
def test_parse_ps_rejects_unreadable_or_ambiguous_output(output: str) -> None:
    with pytest.raises(KafkaControlError):
        parse_ps(output)


def test_state_flags() -> None:
    assert KafkaState("running", "healthy").healthy
    assert not KafkaState("running", "starting").healthy
    assert not KafkaState("exited", "").running


def test_kafka_state_asks_compose_with_an_argv_list(docker: Callable[..., FakeDocker]) -> None:
    fake = docker([RUNNING])
    assert kafka_state().healthy
    assert fake.calls == [["docker", "compose", "ps", "--all", "--format", "json", "kafka"]]


def test_stop_checks_that_the_broker_is_not_running(docker: Callable[..., FakeDocker]) -> None:
    fake = docker([EXITED])
    stop_kafka()
    assert fake.calls[0] == ["docker", "compose", "stop", "kafka"]
    docker([RUNNING])
    with pytest.raises(KafkaControlError, match="still running"):
        stop_kafka()


def test_start_waits_for_healthy_and_checks_it(docker: Callable[..., FakeDocker]) -> None:
    fake = docker([RUNNING])
    start_kafka()
    up = fake.calls[0]
    assert up[:5] == ["docker", "compose", "up", "-d", "--wait"]
    assert up[5] == "--wait-timeout"
    assert up[-1] == "kafka"
    docker([STARTING])
    with pytest.raises(KafkaControlError, match="not healthy"):
        start_kafka()


def test_a_failing_compose_call_raises_with_its_output(docker: Callable[..., FakeDocker]) -> None:
    docker([], fail_on="stop")
    with pytest.raises(KafkaControlError, match="exited 1: boom"):
        stop_kafka()


@pytest.mark.parametrize(
    "error", [FileNotFoundError("docker"), subprocess.TimeoutExpired("docker", 1)]
)
def test_docker_missing_or_hanging_is_a_control_error(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(kafka_control.subprocess, "run", fail)
    with pytest.raises(KafkaControlError):
        kafka_state()


def test_kafka_off_stops_inside_and_starts_after_even_when_the_block_fails(
    docker: Callable[..., FakeDocker],
) -> None:
    fake = docker([EXITED, RUNNING])
    with pytest.raises(RuntimeError, match="test body failed"), kafka_off():
        assert [c[2] for c in fake.calls] == ["stop", "ps"]
        raise RuntimeError("test body failed")
    assert [c[2] for c in fake.calls] == ["stop", "ps", "up", "ps"]
