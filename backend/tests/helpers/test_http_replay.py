"""The HTTP replay helper: strict replay, scrubbed cassettes, record mode refused in CI."""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.conftest import BACKEND_DIR
from tests.helpers.http_replay import (
    CASSETTE_DIR,
    CassetteError,
    Interaction,
    NoMatchingRecordingError,
    ReplayConfig,
    ReplayConfigError,
    ReplayError,
    ReplaySession,
    load_cassette,
    request_key,
    save_cassette,
    scrub_headers,
    scrub_url,
)

RECORDED_URL = "https://source.example.invalid/v1/quotes?symbol=DEMO"
OTHER_URL = "https://source.example.invalid/v1/other"
FORBIDDEN = re.compile(r"authorization|api-key", re.IGNORECASE)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps(
            {"path": self.path.split("?")[0], "echo": self.headers.get("X-Demo", "")}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Authorization", "Bearer demo-server-token")
        self.send_header("X-Api-Key", "demo-server-key")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


@pytest.fixture
def loopback_url() -> Iterator[str]:
    """A local HTTP server on 127.0.0.1 that answers any GET with JSON (no outside network)."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def _config(tmp_path: Path, *, strict: bool = True, record: bool = False) -> ReplayConfig:
    return ReplayConfig(strict=strict, record=record, cassette_dir=tmp_path)


def _write(tmp_path: Path, name: str, interactions: list[Interaction]) -> None:
    save_cassette(tmp_path / f"{name}.json", interactions)


def _interaction(url: str, body: str, status: int = 200) -> Interaction:
    return Interaction("GET", url, status, (("content-type", "text/plain"),), body.encode())


def test_replay_serves_recorded_get(replay) -> None:  # type: ignore[no-untyped-def]
    handle = replay("demo_quotes")
    response = httpx.get(RECORDED_URL)
    assert response.status_code == 200
    assert response.json() == {"symbol": "DEMO", "price": 101.25}
    assert handle.served == [request_key("GET", RECORDED_URL)]


async def test_replay_serves_async_clients(replay) -> None:  # type: ignore[no-untyped-def]
    replay("demo_quotes")
    async with httpx.AsyncClient() as client:
        response = await client.get(RECORDED_URL)
    assert response.json()["symbol"] == "DEMO"


def test_unmatched_request_fails(replay) -> None:  # type: ignore[no-untyped-def]
    handle = replay("demo_quotes")
    with pytest.raises(NoMatchingRecordingError, match="no recording in cassette 'demo_quotes'"):
        httpx.get(OTHER_URL)
    assert handle.expect_unmatched() == [request_key("GET", OTHER_URL)]


def test_unmatched_request_swallowed_by_the_code_still_fails_the_test(tmp_path: Path) -> None:
    _write(tmp_path, "c", [_interaction("https://h.invalid/a", "A")])
    session = ReplaySession(_config(tmp_path))
    session.use("c")
    with contextlib.suppress(NoMatchingRecordingError):
        httpx.get("https://h.invalid/b")
    with pytest.raises(NoMatchingRecordingError, match="https://h.invalid/b"):
        session.close()


def test_method_and_query_are_part_of_the_match(tmp_path: Path) -> None:
    _write(tmp_path, "c", [_interaction("https://h.invalid/a?x=1&y=2", "A")])
    session = ReplaySession(_config(tmp_path))
    handle = session.use("c")
    assert httpx.get("https://h.invalid/a?y=2&x=1").text == "A"
    with pytest.raises(NoMatchingRecordingError):
        httpx.post("https://h.invalid/a?x=1&y=2")
    with pytest.raises(NoMatchingRecordingError):
        httpx.get("https://h.invalid/a?x=1")
    handle.expect_unmatched()
    session.close()


def test_credential_query_param_is_ignored_when_matching(tmp_path: Path) -> None:
    _write(tmp_path, "c", [_interaction("https://h.invalid/a?q=1&api_key=demo", "A")])
    session = ReplaySession(_config(tmp_path))
    session.use("c")
    assert httpx.get("https://h.invalid/a?q=1&api_key=another-demo-value").text == "A"
    session.close()


def test_repeated_requests_follow_the_recorded_order_then_repeat_the_last(tmp_path: Path) -> None:
    url = "https://h.invalid/a"
    _write(tmp_path, "c", [_interaction(url, "first"), _interaction(url, "second", status=503)])
    session = ReplaySession(_config(tmp_path))
    session.use("c")
    assert [httpx.get(url).text for _ in range(3)] == ["first", "second", "second"]
    session.close()


def test_non_strict_lets_an_unmatched_request_reach_the_network(
    tmp_path: Path, loopback_url: str
) -> None:
    _write(tmp_path, "c", [_interaction("https://h.invalid/a", "A")])
    session = ReplaySession(_config(tmp_path, strict=False))
    handle = session.use("c")
    assert httpx.get("https://h.invalid/a").text == "A"
    assert httpx.get(f"{loopback_url}/live").json()["path"] == "/live"
    assert handle.unmatched == []
    session.close()


def test_one_cassette_per_test_and_bad_names_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, "c", [_interaction("https://h.invalid/a", "A")])
    session = ReplaySession(_config(tmp_path))
    with pytest.raises(ReplayError, match="bad cassette name"):
        session.use("../escape")
    session.use("c")
    with pytest.raises(ReplayError, match="already active"):
        session.use("c")
    session.close()


def test_missing_cassette_says_how_to_record(tmp_path: Path) -> None:
    session = ReplaySession(_config(tmp_path))
    with pytest.raises(CassetteError, match="REPLAY_RECORD=1"):
        session.use("absent")
    session.close()


def test_close_without_a_cassette_is_a_no_op(tmp_path: Path) -> None:
    ReplaySession(_config(tmp_path)).close()


def test_no_auth_headers_in_cassettes() -> None:
    """Same check as `grep -il 'authorization\\|api-key' backend/tests/fixtures/cassettes`."""
    cassettes = sorted(CASSETTE_DIR.rglob("*"))
    assert any(p.suffix == ".json" for p in cassettes), "no cassette stored"
    offenders = [p.name for p in cassettes if p.is_file() and FORBIDDEN.search(p.read_text())]
    assert offenders == []
    for path in CASSETTE_DIR.glob("*.json"):
        assert load_cassette(path)


def test_scrub_drops_credential_headers_and_framing() -> None:
    headers = [
        ("Authorization", "Bearer x"),
        ("X-Api-Key", "k"),
        ("anthropic-api-key", "k"),
        ("Set-Cookie", "s=1"),
        ("X-Auth-Token", "t"),
        ("Content-Encoding", "gzip"),
        ("Content-Length", "12"),
        ("Content-Type", "application/json"),
        ("ETag", "abc"),
    ]
    assert scrub_headers(headers) == [("content-type", "application/json"), ("etag", "abc")]


def test_scrub_url_drops_credential_params_and_fragment() -> None:
    url = "https://h.invalid/p?symbol=A&api_key=1&Token=2&key=3&q=%20x#frag"
    assert scrub_url(url) == "https://h.invalid/p?symbol=A&q=+x"


def test_load_rejects_a_cassette_that_mentions_a_credential(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"version": 1, "interactions": [], "note": "Authorization: Bearer x"}')
    with pytest.raises(CassetteError, match="authorization or api-key"):
        load_cassette(path)


@pytest.mark.parametrize(
    "content", ["not json", '{"version": 2, "interactions": []}', '{"version": 1}']
)
def test_load_rejects_malformed_cassettes(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(CassetteError):
        load_cassette(path)


def test_save_refuses_a_body_that_mentions_a_credential(tmp_path: Path) -> None:
    leaky = _interaction("https://h.invalid/a", "echo of your api-key header")
    with pytest.raises(CassetteError, match="not saving"):
        save_cassette(tmp_path / "leaky.json", [leaky])
    assert not (tmp_path / "leaky.json").exists()


def test_binary_bodies_round_trip(tmp_path: Path) -> None:
    binary = Interaction("GET", "https://h.invalid/b", 200, (), bytes(range(256)))
    save_cassette(tmp_path / "b.json", [binary])
    assert load_cassette(tmp_path / "b.json") == [binary]


def test_record_then_replay_round_trip_is_scrubbed(tmp_path: Path, loopback_url: str) -> None:
    url = f"{loopback_url}/data?symbol=A&api_key=demo-key-123"
    recorder = ReplaySession(_config(tmp_path, record=True))
    recorder.use("rec")
    live = httpx.get(url, headers={"X-Demo": "1", "Authorization": "Bearer demo"})
    assert live.json() == {"path": "/data", "echo": "1"}
    assert "authorization" not in live.headers
    recorder.close()

    stored = (tmp_path / "rec.json").read_text()
    assert not FORBIDDEN.search(stored)
    assert "demo-key-123" not in stored
    assert "demo-server-token" not in stored

    player = ReplaySession(_config(tmp_path))
    player.use("rec")
    assert httpx.get(url).json() == live.json()
    player.close()


async def test_record_mode_captures_async_clients(tmp_path: Path, loopback_url: str) -> None:
    recorder = ReplaySession(_config(tmp_path, record=True))
    recorder.use("arec")
    async with httpx.AsyncClient() as client:
        live = await client.get(f"{loopback_url}/async")
    recorder.close()
    [stored] = load_cassette(tmp_path / "arec.json")
    assert stored.url.endswith("/async")
    assert json.loads(stored.body) == live.json()


def test_record_with_nothing_requested_writes_no_cassette(tmp_path: Path) -> None:
    recorder = ReplaySession(_config(tmp_path, record=True))
    recorder.use("empty")
    recorder.close()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("environ", "strict", "record"),
    [
        ({}, True, False),
        ({"REPLAY_STRICT": "0"}, False, False),
        ({"REPLAY_STRICT": "1", "CI": "true"}, True, False),
        ({"REPLAY_RECORD": "1"}, True, True),
        ({"REPLAY_RECORD": "0", "CI": "true"}, True, False),
    ],
)
def test_config_from_env(environ: dict[str, str], strict: bool, record: bool) -> None:
    config = ReplayConfig.from_env(environ)
    assert (config.strict, config.record, config.cassette_dir) == (strict, record, CASSETTE_DIR)


def test_config_cassette_dir_override(tmp_path: Path) -> None:
    assert ReplayConfig.from_env({"REPLAY_CASSETTE_DIR": str(tmp_path)}).cassette_dir == tmp_path


def test_config_refuses_live_requests_in_ci() -> None:
    with pytest.raises(ReplayConfigError, match="REPLAY_STRICT=0 is refused"):
        ReplayConfig.from_env({"CI": "true", "REPLAY_STRICT": "0"})


def test_record_refused_in_ci() -> None:
    """`REPLAY_RECORD=1 CI=true` makes a run that uses the replay fixture exit non-zero."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE", "PYTEST_ADDOPTS"))}
    env.update(REPLAY_RECORD="1", CI="true")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "tests/helpers/test_http_replay.py::test_replay_serves_recorded_get",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "REPLAY_RECORD=1 is refused when CI=true" in result.stdout + result.stderr
