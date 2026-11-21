import json

import pytest
from fastapi.testclient import TestClient

from app import IDENTITY_PATH, create_app
from config import Settings
from tokens import digest_header


@pytest.fixture
def client():
    return TestClient(create_app(Settings()))


def parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        fields["data"] = json.loads(fields["data"])
        events.append(fields)
    return events


def test_echo_never_returns_raw_authorization(client):
    value = "Bearer SENTINEL_AAA"
    response = client.get("/echo", headers={"Authorization": value})
    assert response.status_code == 200
    assert "SENTINEL_AAA" not in response.text
    assert response.json()["headers"]["authorization"] == digest_header(value)
    assert response.json()["headers"]["authorization"]["length"] == len(value)


def test_echo_returns_gateway_headers_as_received_and_accepts_post(client):
    sent = {
        "X-RL-Key": "k",
        "X-Request-Id": "r",
        "traceparent": "t",
        "X-User-Id": "u",
        "X-Tenant-Id": "n",
    }
    response = client.post("/echo", headers=sent, content=b"secret-body")
    body = response.json()
    assert response.status_code == 200
    assert body["method"] == "POST"
    assert body["path"] == "/echo"
    assert body["serverless_auth"] is None
    for name, value in sent.items():
        assert body["headers"][name.lower()] == value
    assert "secret-body" not in response.text


def test_echo_redacts_other_credential_headers(client):
    response = client.get(
        "/echo",
        headers={
            "Proxy-Authorization": "Basic SENTINEL_BBB",
            "X-Serverless-Authorization": "SENTINEL_CCC",
        },
    )
    assert "SENTINEL_BBB" not in response.text
    assert "SENTINEL_CCC" not in response.text
    assert response.json()["headers"]["proxy-authorization"]["length"] == len("Basic SENTINEL_BBB")


def test_sse_sequence(client):
    response = client.get("/sse", params={"events": 50, "interval_ms": 20})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_sse(response.text)
    assert len(events) == 50
    seqs = [e["data"]["seq"] for e in events]
    stamps = [e["data"]["emit_ts_ns"] for e in events]
    assert seqs == list(range(1, 51))
    assert [int(e["id"]) for e in events] == seqs
    assert {e["event"] for e in events} == {"tick"}
    assert all(a < b for a, b in zip(stamps, stamps[1:], strict=False))


def test_sse_timestamps_strictly_increase_with_zero_interval(client):
    events = parse_sse(client.get("/sse", params={"events": 200, "interval_ms": 0}).text)
    stamps = [e["data"]["emit_ts_ns"] for e in events]
    assert len(stamps) == 200
    assert all(a < b for a, b in zip(stamps, stamps[1:], strict=False))


def test_sse_defaults_and_event_name_knob():
    small = TestClient(create_app(Settings(sse_event_name="beat")))
    events = parse_sse(small.get("/sse", params={"interval_ms": 0}).text)
    assert len(events) == 10
    assert {e["event"] for e in events} == {"beat"}


@pytest.mark.parametrize(
    "params",
    [
        {"events": 0},
        {"events": -1},
        {"events": "x"},
        {"interval_ms": -1},
        {"events": 6},
        {"events": 1, "interval_ms": 51},
    ],
)
def test_sse_rejects_out_of_bounds(params):
    limited = TestClient(
        create_app(Settings(sse_max_events=5, sse_max_interval_ms=50, sse_max_duration_s=1))
    )
    assert limited.get("/sse", params=params).status_code == 422


def test_sse_rejects_total_duration_over_cap():
    capped = TestClient(
        create_app(Settings(sse_max_events=100, sse_max_interval_ms=500, sse_max_duration_s=1))
    )
    assert capped.get("/sse", params={"events": 5, "interval_ms": 250}).status_code == 422


def test_sse_accepts_the_exact_bounds():
    limited = TestClient(
        create_app(Settings(sse_max_events=4, sse_max_interval_ms=250, sse_max_duration_s=1))
    )
    response = limited.get("/sse", params={"events": 4, "interval_ms": 250})
    assert response.status_code == 200
    assert len(parse_sse(response.text)) == 4


def test_metadata_mock_token_decodes_in_echo(client):
    identity = client.get(IDENTITY_PATH, params={"audience": "https://x"})
    assert identity.status_code == 200
    assert identity.headers["content-type"].startswith("text/plain")
    assert identity.headers["metadata-flavor"] == "Google"
    token = identity.text
    echo = client.get("/echo", headers={"X-Serverless-Authorization": f"Bearer {token}"})
    claims = echo.json()["serverless_auth"]
    assert claims["aud"] == "https://x"
    assert claims["email"] == Settings().metadata_email
    assert isinstance(claims["exp"], int)
    assert token not in echo.text


def test_echo_malformed_serverless_authorization_is_an_error_object_not_500(client):
    response = client.get("/echo", headers={"X-Serverless-Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 200
    assert "error" in response.json()["serverless_auth"]


@pytest.mark.parametrize("params", [{}, {"audience": ""}, {"audience": "a" * 2049}])
def test_metadata_mock_rejects_bad_audience(client, params):
    assert client.get(IDENTITY_PATH, params=params).status_code == 422


def test_create_app_reads_settings_from_env_by_default(monkeypatch):
    monkeypatch.setenv("STUB_METADATA_EMAIL", "env@example")
    default_client = TestClient(create_app())
    token = default_client.get(IDENTITY_PATH, params={"audience": "a"}).text
    claims = default_client.get("/echo", headers={"X-Serverless-Authorization": token}).json()
    assert claims["serverless_auth"]["email"] == "env@example"


def test_docs_endpoints_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
