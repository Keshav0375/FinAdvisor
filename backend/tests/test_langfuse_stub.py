from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from tests.stubs import langfuse_stub as stub_mod
from tests.stubs.langfuse_stub import LangfuseStub

AUTH = (stub_mod.DEMO_PUBLIC_KEY, stub_mod.DEMO_SECRET_KEY)
TRACES = "/api/public/traces"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def http() -> Iterator[httpx.Client]:
    with httpx.Client(auth=AUTH, timeout=5.0) as client:
        yield client


def batch_delete(http: httpx.Client, stub: LangfuseStub, *ids: str) -> httpx.Response:
    return http.request("DELETE", stub.url + TRACES, json={"traceIds": list(ids)})


# AC1: ignore mode, batch delete then GET.
def test_ignore_mode_returns_202_and_trace_stays(http: httpx.Client) -> None:
    clock = FakeClock()
    with LangfuseStub("ignore", clock=clock) as stub:
        stub.add_trace("t1")
        resp = batch_delete(http, stub, "t1")
        assert resp.status_code == 202
        assert "message" in resp.json()
        clock.now += 10_000_000
        assert http.get(f"{stub.url}{TRACES}/t1").status_code == 200
        assert stub.has_trace("t1")


# AC2: error mode, first delete 500, second 202.
def test_error_mode_first_500_then_202(http: httpx.Client) -> None:
    with LangfuseStub("error") as stub:
        stub.add_trace("t1")
        first = batch_delete(http, stub, "t1")
        assert first.status_code == 500
        assert stub.has_trace("t1")
        second = batch_delete(http, stub, "t1")
        assert second.status_code == 202
        assert not stub.has_trace("t1")
        assert [c.status for c in stub.calls if c.method == "DELETE"] == [500, 202]


def test_error_mode_counts_single_and_batch_calls_together(http: httpx.Client) -> None:
    with LangfuseStub("error", error_count=2) as stub:
        stub.add_trace("t1")
        assert http.delete(f"{stub.url}{TRACES}/t1").status_code == 500
        assert batch_delete(http, stub, "t1").status_code == 500
        assert http.delete(f"{stub.url}{TRACES}/t1").status_code == 202


def test_honour_mode_is_eventually_consistent(http: httpx.Client) -> None:
    clock = FakeClock()
    with LangfuseStub("honour", delete_delay_s=90.0, clock=clock) as stub:
        stub.add_trace("t1")
        assert http.delete(f"{stub.url}{TRACES}/t1").status_code == 202
        assert http.get(f"{stub.url}{TRACES}/t1").status_code == 200
        clock.now += 89.0
        assert http.get(f"{stub.url}{TRACES}/t1").status_code == 200
        clock.now += 1.0
        assert http.get(f"{stub.url}{TRACES}/t1").status_code == 404


def test_honour_mode_default_removes_at_once(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        stub.add_trace("t1")
        stub.add_trace("t2")
        assert batch_delete(http, stub, "t1", "t2").status_code == 202
        assert http.get(f"{stub.url}{TRACES}/t1").status_code == 404
        assert http.get(f"{stub.url}{TRACES}/t2").status_code == 404


def test_get_missing_trace_is_404(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        resp = http.get(f"{stub.url}{TRACES}/nope")
        assert resp.status_code == 404
        assert "message" in resp.json()


def test_single_delete_of_missing_trace_is_404(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        assert http.delete(f"{stub.url}{TRACES}/nope").status_code == 404


def test_batch_delete_ignores_unknown_ids(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        stub.add_trace("t1")
        assert batch_delete(http, stub, "t1", "nope").status_code == 202
        assert not stub.has_trace("t1")


@pytest.mark.parametrize(
    "body",
    [{}, {"traceIds": []}, {"traceIds": "t1"}, {"traceIds": [1]}, {"ids": ["t1"]}, ["t1"]],
)
def test_batch_delete_rejects_bad_body_with_400(http: httpx.Client, body: object) -> None:
    with LangfuseStub("honour") as stub:
        assert http.request("DELETE", stub.url + TRACES, json=body).status_code == 400


def test_batch_delete_rejects_invalid_json_with_400(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        resp = http.request("DELETE", stub.url + TRACES, content=b"{not json")
        assert resp.status_code == 400


def test_bad_or_missing_basic_auth_is_401() -> None:
    with LangfuseStub("honour") as stub:
        stub.add_trace("t1")
        assert httpx.get(f"{stub.url}{TRACES}/t1").status_code == 401
        wrong = httpx.delete(f"{stub.url}{TRACES}/t1", auth=("demo-public-key", "wrong"))
        assert wrong.status_code == 401
        malformed = httpx.get(f"{stub.url}{TRACES}/t1", headers={"Authorization": "Basic %%%"})
        assert malformed.status_code == 401
        assert stub.has_trace("t1")


def test_unknown_route_is_404(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        assert http.get(f"{stub.url}/api/public/unknown").status_code == 404
        assert http.post(f"{stub.url}/api/public/unknown", json={}).status_code == 404
        assert http.delete(f"{stub.url}/api/public/unknown").status_code == 404


def test_ingestion_creates_traces(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        body = {
            "batch": [
                {
                    "id": "e1",
                    "type": "trace-create",
                    "timestamp": "2026-11-23T10:00:00.000Z",
                    "body": {"id": "t9", "name": "spike"},
                },
                {"id": "e2", "type": "score-create", "timestamp": "x", "body": {}},
            ]
        }
        resp = http.post(f"{stub.url}/api/public/ingestion", json=body)
        assert resp.status_code == 207
        assert resp.json()["successes"] == [{"id": "e1", "status": 201}]
        assert resp.json()["errors"] == [{"id": "e2", "status": 400, "message": "unsupported"}]
        assert http.get(f"{stub.url}{TRACES}/t9").status_code == 200


@pytest.mark.parametrize("body", [{}, {"batch": "x"}, {"batch": [1]}])
def test_ingestion_rejects_bad_body(http: httpx.Client, body: object) -> None:
    with LangfuseStub("honour") as stub:
        assert http.post(f"{stub.url}/api/public/ingestion", json=body).status_code == 400


def test_ingestion_rejects_invalid_json(http: httpx.Client) -> None:
    with LangfuseStub("honour") as stub:
        resp = http.post(f"{stub.url}/api/public/ingestion", content=b"nope")
        assert resp.status_code == 400


def test_accept_status_knob_sets_the_success_code(http: httpx.Client) -> None:
    with LangfuseStub("honour", accept_status=200) as stub:
        stub.add_trace("t1")
        resp = http.delete(f"{stub.url}{TRACES}/t1")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Trace deleted successfully"}


def test_env_overrides_the_knobs() -> None:
    env = {
        "LANGFUSE_STUB_ACCEPT_STATUS": "200",
        "LANGFUSE_STUB_DELETE_DELAY_S": "7.5",
        "LANGFUSE_STUB_ERROR_COUNT": "3",
    }
    stub = LangfuseStub("error", environ=env)
    assert stub.accept_status == 200
    assert stub.delete_delay_s == 7.5
    assert stub.error_count == 3


def test_defaults_without_env() -> None:
    stub = LangfuseStub("honour", environ={})
    assert stub.accept_status == 202
    assert stub.delete_delay_s == 0.0
    assert stub.error_count == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LANGFUSE_STUB_ACCEPT_STATUS", "teapot"),
        ("LANGFUSE_STUB_ACCEPT_STATUS", "404"),
        ("LANGFUSE_STUB_DELETE_DELAY_S", "-1"),
        ("LANGFUSE_STUB_DELETE_DELAY_S", "soon"),
        ("LANGFUSE_STUB_ERROR_COUNT", "-2"),
    ],
)
def test_bad_env_value_fails_fast(key: str, value: str) -> None:
    with pytest.raises(ValueError, match=key):
        LangfuseStub("honour", environ={key: value})


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="mode"):
        LangfuseStub("flaky", environ={})


def test_url_before_start_raises() -> None:
    with pytest.raises(RuntimeError, match="not started"):
        _ = LangfuseStub("honour", environ={}).url
