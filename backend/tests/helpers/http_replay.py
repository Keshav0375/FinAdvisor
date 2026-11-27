"""Recorded-fixture HTTP replay for connector tests (VCR-style, on respx).

A cassette is a JSON file in `tests/fixtures/cassettes/` holding recorded request/response pairs.
The `replay` fixture is a factory: `replay("demo_quotes")` serves that cassette to every httpx
client (sync and async, so also the SDKs built on httpx) for the rest of the test.

Knobs (environment, read once per test by `ReplayConfig.from_env`):
  REPLAY_STRICT        1 (default) fails any request with no recording; 0 lets it reach the network.
                       Refused when CI=true: the PR gate never calls a live source.
  REPLAY_RECORD        1 records the real responses into the cassette instead of replaying.
                       Local only: refused when CI=true.
  REPLAY_CASSETTE_DIR  cassette directory (default tests/fixtures/cassettes).

Cassettes never hold credentials: header and query values whose name looks like a credential are
dropped before anything is written, and a cassette that still mentions one is refused on load and
on save.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import pytest
import respx

CASSETTE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "cassettes"
CASSETTE_VERSION = 1

# Same words as the acceptance check `grep -il 'authorization\|api-key'` over the cassettes.
FORBIDDEN_TEXT = re.compile(r"authorization|api-key", re.IGNORECASE)
CASSETTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# A header or query name is a credential when it contains one of these (case and `_`/`-` blind).
_CREDENTIAL_WORDS = ("authorization", "api-key", "apikey", "token", "secret", "cookie", "password")
_CREDENTIAL_NAMES = frozenset({"key", "auth", "signature", "sig"})
# Transfer framing of the live response; the stored body is already decoded.
_FRAMING_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class ReplayError(Exception):
    """Base class of the replay helper's own failures."""


class ReplayConfigError(ReplayError):
    """The replay environment asks for something that is not allowed (record or live in CI)."""


class CassetteError(ReplayError):
    """A cassette is missing, malformed, or mentions a credential."""


class NoMatchingRecordingError(ReplayError):
    """A request has no recording in the cassette (strict mode)."""


@dataclass(frozen=True)
class ReplayConfig:
    strict: bool
    record: bool
    cassette_dir: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ReplayConfig:
        in_ci = environ.get("CI", "").strip().lower() == "true"
        record = environ.get("REPLAY_RECORD", "") == "1"
        strict = environ.get("REPLAY_STRICT", "1") != "0"
        if in_ci and record:
            raise ReplayConfigError("REPLAY_RECORD=1 is refused when CI=true: record locally only")
        if in_ci and not strict:
            raise ReplayConfigError("REPLAY_STRICT=0 is refused when CI=true: the gate is offline")
        raw_dir = environ.get("REPLAY_CASSETTE_DIR", "").strip()
        return cls(
            strict=strict, record=record, cassette_dir=Path(raw_dir) if raw_dir else CASSETTE_DIR
        )


def _is_credential(name: str) -> bool:
    normal = name.lower().replace("_", "-")
    return normal in _CREDENTIAL_NAMES or any(word in normal for word in _CREDENTIAL_WORDS)


def scrub_headers(headers: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Headers without credentials and without transfer framing, names lower-cased."""
    return [
        (name.lower(), value)
        for name, value in headers
        if not _is_credential(name) and name.lower() not in _FRAMING_HEADERS
    ]


def scrub_url(url: str) -> str:
    """The URL without query parameters whose name looks like a credential."""
    parts = urlsplit(url)
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_credential(k)
    ]
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def request_key(method: str, url: str) -> str:
    """What a request is matched on: method and scrubbed URL with the query sorted."""
    parts = urlsplit(scrub_url(url))
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return f"{method.upper()} {urlunsplit(parts._replace(query=query))}"


@dataclass(frozen=True)
class Interaction:
    method: str
    url: str
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    @property
    def key(self) -> str:
        return request_key(self.method, self.url)

    def to_json(self) -> dict[str, Any]:
        try:
            body, encoding = self.body.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            body, encoding = base64.b64encode(self.body).decode("ascii"), "base64"
        return {
            "request": {"method": self.method.upper(), "url": scrub_url(self.url)},
            "response": {
                "status": self.status,
                "headers": [list(pair) for pair in self.headers],
                "body": body,
                "body_encoding": encoding,
            },
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Interaction:
        request, response = raw["request"], raw["response"]
        body = response["body"]
        data = (
            base64.b64decode(body) if response.get("body_encoding") == "base64" else body.encode()
        )
        return cls(
            method=str(request["method"]).upper(),
            url=str(request["url"]),
            status=int(response["status"]),
            headers=tuple((str(k), str(v)) for k, v in response["headers"]),
            body=data,
        )

    def to_response(self) -> httpx.Response:
        return httpx.Response(self.status, headers=list(self.headers), content=self.body)


def load_cassette(path: Path) -> list[Interaction]:
    """The interactions of a cassette; refuses a file that mentions a credential."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CassetteError(
            f"cassette not found: {path} (record it locally with REPLAY_RECORD=1)"
        ) from None
    if FORBIDDEN_TEXT.search(text):
        raise CassetteError(f"cassette {path.name} mentions an authorization or api-key value")
    try:
        data = json.loads(text)
        if data["version"] != CASSETTE_VERSION:
            raise CassetteError(f"cassette {path.name}: unsupported version {data['version']!r}")
        return [Interaction.from_json(item) for item in data["interactions"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise CassetteError(f"cassette {path.name} is malformed: {exc!r}") from exc


def save_cassette(path: Path, interactions: Sequence[Interaction]) -> None:
    """Write the cassette (scrubbed); refuses content that still mentions a credential."""
    text = json.dumps(
        {"version": CASSETTE_VERSION, "interactions": [i.to_json() for i in interactions]},
        indent=2,
    )
    if FORBIDDEN_TEXT.search(text):
        raise CassetteError(
            f"not saving {path.name}: a response mentions an authorization or api-key value"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


@dataclass
class ReplayHandle:
    """What a test sees of an active cassette."""

    cassette: str
    served: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def expect_unmatched(self) -> list[str]:
        """The requests that had no recording, cleared: a test that provokes them on purpose
        calls this so that teardown does not fail it."""
        taken, self.unmatched = list(self.unmatched), []
        return taken


def _to_interaction(request: httpx.Request, response: httpx.Response) -> Interaction:
    return Interaction(
        method=request.method,
        url=scrub_url(str(request.url)),
        status=response.status_code,
        headers=tuple(scrub_headers(list(response.headers.multi_items()))),
        body=response.content,
    )


def _rebuild(response: httpx.Response, stored: Interaction) -> httpx.Response:
    """The live response as the caller gets it in record mode (body already read)."""
    return httpx.Response(
        stored.status,
        headers=list(stored.headers),
        content=stored.body,
        extensions=response.extensions,
    )


class ReplaySession:
    """One active cassette: serves it (or records into it) until `close`."""

    def __init__(self, config: ReplayConfig) -> None:
        self._config = config
        self._stack = ExitStack()
        self._handle: ReplayHandle | None = None
        self._recorded: list[Interaction] = []
        self._record_path: Path | None = None

    def use(self, cassette: str) -> ReplayHandle:
        if self._handle is not None:
            raise ReplayError(f"cassette {self._handle.cassette!r} is already active in this test")
        if not CASSETTE_NAME.fullmatch(cassette):
            raise ReplayError(f"bad cassette name {cassette!r}: letters, digits, _ . - only")
        path = self._config.cassette_dir / f"{cassette}.json"
        self._handle = ReplayHandle(cassette)
        if self._config.record:
            self._start_recording(path)
        else:
            self._start_replaying(load_cassette(path))
        return self._handle

    def _start_replaying(self, interactions: Sequence[Interaction]) -> None:
        handle = self._handle
        assert handle is not None
        by_key: dict[str, list[Interaction]] = {}
        for item in interactions:
            by_key.setdefault(item.key, []).append(item)
        position: dict[str, int] = {}

        def serve(request: httpx.Request) -> httpx.Response | httpx.Request:
            key = request_key(request.method, str(request.url))
            candidates = by_key.get(key)
            if candidates:
                index = position.get(key, 0)
                position[key] = index + 1
                handle.served.append(key)
                # The recorded sequence in order; the last recording answers any repeat.
                return candidates[min(index, len(candidates) - 1)].to_response()
            if not self._config.strict:
                return request  # respx passes a returned request through to the network
            handle.unmatched.append(key)
            raise NoMatchingRecordingError(
                f"no recording in cassette {handle.cassette!r} for {key}"
            )

        router = respx.MockRouter(assert_all_called=False, assert_all_mocked=False)
        router.route().mock(side_effect=serve)
        router.start()
        self._stack.callback(router.stop)

    def _start_recording(self, path: Path) -> None:
        self._record_path = path
        sync_original = httpx.HTTPTransport.handle_request
        async_original = httpx.AsyncHTTPTransport.handle_async_request

        def record_sync(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
            response = sync_original(transport, request)
            response.read()
            stored = _to_interaction(request, response)
            self._recorded.append(stored)
            return _rebuild(response, stored)

        async def record_async(
            transport: httpx.AsyncHTTPTransport, request: httpx.Request
        ) -> httpx.Response:
            response = await async_original(transport, request)
            await response.aread()
            stored = _to_interaction(request, response)
            self._recorded.append(stored)
            return _rebuild(response, stored)

        for target, name, wrapper in (
            (httpx.HTTPTransport, "handle_request", record_sync),
            (httpx.AsyncHTTPTransport, "handle_async_request", record_async),
        ):
            patcher = mock.patch.object(target, name, wrapper)
            patcher.start()
            self._stack.callback(patcher.stop)

    def close(self) -> None:
        """Stop serving; save the recording; fail when a request had no recording."""
        self._stack.close()
        handle = self._handle
        if self._record_path is not None and self._recorded:
            save_cassette(self._record_path, self._recorded)
        if handle is not None and handle.unmatched:
            raise NoMatchingRecordingError(
                f"cassette {handle.cassette!r} had no recording for: {', '.join(handle.unmatched)}"
            )


@pytest.fixture
def replay() -> Iterator[Callable[[str], ReplayHandle]]:
    """`replay(name)` serves cassette `name` to every httpx client until the test ends; a request
    with no recording raises NoMatchingRecordingError and still fails the test if it was caught."""
    session = ReplaySession(ReplayConfig.from_env(os.environ))
    try:
        yield session.use
    finally:
        session.close()
