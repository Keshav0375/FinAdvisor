"""In-process stub of the Langfuse public trace-delete API (E12-T3 spike, used later by test #24).

The shapes follow the Langfuse API reference (see docs/adr/langfuse-delete-behaviour.md):
DELETE /api/public/traces/{traceId}, DELETE /api/public/traces with a JSON body {"traceIds": [...]},
GET /api/public/traces/{traceId}, and basic auth (public key : secret key). It also takes
POST /api/public/ingestion with trace-create events, so the live procedure can run against it.

Three modes, fixed at construction:
- honour: delete is accepted and the trace stops being returned after the delete delay;
- ignore: delete is accepted and the trace stays forever (the case test #24 needs);
- error: the first `error_count` delete calls fail with 500 and change nothing, then honour.

Nothing here is a measurement of Langfuse Cloud. The accept status, the delete delay and the error
count are knobs with environment overrides; the clock is injected so tests need no sleeps.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any

# Demo keys, the same pair as the E0-T1 spike. Not secrets.
DEMO_PUBLIC_KEY = "demo-public-key"
DEMO_SECRET_KEY = "demo-secret-key"

MODES = ("honour", "ignore", "error")

# The ticket, ADR-030 and test row #24 say 202. The API reference documents 200 for a delete (the
# ADR records the difference), so the code is a knob and the client under test must accept any 2xx.
DEFAULT_ACCEPT_STATUS = 202
# 0 means a deleted trace is gone at the next read; a positive value models eventual consistency.
DEFAULT_DELETE_DELAY_S = 0.0
DEFAULT_ERROR_COUNT = 1

ENV_ACCEPT_STATUS = "LANGFUSE_STUB_ACCEPT_STATUS"
ENV_DELETE_DELAY_S = "LANGFUSE_STUB_DELETE_DELAY_S"
ENV_ERROR_COUNT = "LANGFUSE_STUB_ERROR_COUNT"

BIND_HOST = "127.0.0.1"
# How often the server loop checks for shutdown; the 0.5 s default made every test wait.
POLL_INTERVAL_S = 0.02
TRACES_PATH = "/api/public/traces"
INGESTION_PATH = "/api/public/ingestion"
TRACE_CREATE = "trace-create"


@dataclass(frozen=True)
class StubCall:
    method: str
    path: str
    status: int


def _env_number(
    environ: Mapping[str, str], key: str, default: float, parse: Callable[[str], float]
) -> float:
    raw = environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = parse(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{key} must not be negative, got {raw!r}")
    return value


class LangfuseStub:
    def __init__(
        self,
        mode: str,
        *,
        accept_status: int | None = None,
        delete_delay_s: float | None = None,
        error_count: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        environ: Mapping[str, str] | None = None,
        public_key: str = DEMO_PUBLIC_KEY,
        secret_key: str = DEMO_SECRET_KEY,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        env = os.environ if environ is None else environ
        if accept_status is None:
            accept_status = int(
                _env_number(env, ENV_ACCEPT_STATUS, DEFAULT_ACCEPT_STATUS, lambda s: int(s))
            )
            if not 200 <= accept_status <= 299:
                raise ValueError(f"{ENV_ACCEPT_STATUS} must be a 2xx code, got {accept_status}")
        if delete_delay_s is None:
            delete_delay_s = _env_number(env, ENV_DELETE_DELAY_S, DEFAULT_DELETE_DELAY_S, float)
        if error_count is None:
            error_count = int(
                _env_number(env, ENV_ERROR_COUNT, DEFAULT_ERROR_COUNT, lambda s: int(s))
            )
        self.mode = mode
        self.accept_status = accept_status
        self.delete_delay_s = delete_delay_s
        self.error_count = error_count
        self.calls: list[StubCall] = []
        self._clock = clock
        self._expected_auth = "Basic " + base64.b64encode(
            f"{public_key}:{secret_key}".encode()
        ).decode("ascii")
        self._lock = threading.Lock()
        self._traces: set[str] = set()
        self._gone_at: dict[str, float] = {}
        self._failed_deletes = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> LangfuseStub:
        self._server = ThreadingHTTPServer((BIND_HOST, 0), _make_handler(self))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": POLL_INTERVAL_S},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("stub not started: use it as a context manager")
        return f"http://{BIND_HOST}:{self._server.server_address[1]}"

    def add_trace(self, trace_id: str) -> None:
        with self._lock:
            self._traces.add(trace_id)

    def has_trace(self, trace_id: str) -> bool:
        with self._lock:
            return self._visible(trace_id)

    def authorized(self, header: str | None) -> bool:
        return header == self._expected_auth

    def record(self, method: str, path: str, status: int) -> None:
        with self._lock:
            self.calls.append(StubCall(method, path, status))

    def create_traces(self, trace_ids: list[str]) -> None:
        with self._lock:
            self._traces.update(trace_ids)

    def get_trace(self, trace_id: str) -> bool:
        with self._lock:
            return self._visible(trace_id)

    def delete(self, trace_ids: list[str], *, require_known: bool) -> int:
        """Apply one delete call and return its HTTP status."""
        with self._lock:
            if self.mode == "error" and self._failed_deletes < self.error_count:
                self._failed_deletes += 1
                return 500
            if require_known and any(not self._visible(t) for t in trace_ids):
                return 404
            if self.mode != "ignore":
                now = self._clock()
                for trace_id in trace_ids:
                    if trace_id in self._traces:
                        self._gone_at.setdefault(trace_id, now + self.delete_delay_s)
            return self.accept_status

    def _visible(self, trace_id: str) -> bool:
        if trace_id not in self._traces:
            return False
        gone_at = self._gone_at.get(trace_id)
        return gone_at is None or self._clock() < gone_at


def _make_handler(stub: LangfuseStub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return None

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            stub.record(self.command, self.path, status)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if stub.authorized(self.headers.get("Authorization")):
                return True
            self._send(401, {"message": "Unauthorized"})
            return False

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"null")
            except json.JSONDecodeError:
                return None

        def _trace_id(self) -> str | None:
            prefix = TRACES_PATH + "/"
            if self.path.startswith(prefix) and len(self.path) > len(prefix):
                return self.path[len(prefix) :]
            return None

        def do_GET(self) -> None:
            if not self._authorized():
                return
            trace_id = self._trace_id()
            if trace_id is None:
                self._send(404, {"message": "Not found"})
            elif stub.get_trace(trace_id):
                self._send(200, {"id": trace_id})
            else:
                self._send(404, {"message": f"Trace {trace_id} not found"})

        def do_DELETE(self) -> None:
            if not self._authorized():
                return
            trace_id = self._trace_id()
            if trace_id is not None:
                status = stub.delete([trace_id], require_known=True)
                self._send_delete(status, "Trace deleted successfully", trace_id)
            elif self.path == TRACES_PATH:
                body = self._body()
                ids = body.get("traceIds") if isinstance(body, dict) else None
                if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                    self._send(400, {"message": "traceIds must be a non-empty list of strings"})
                    return
                status = stub.delete(ids, require_known=False)
                self._send_delete(status, "Traces deleted successfully", None)
            else:
                self._send(404, {"message": "Not found"})

        def _send_delete(self, status: int, message: str, trace_id: str | None) -> None:
            if status == 404:
                self._send(404, {"message": f"Trace {trace_id} not found"})
            elif status == 500:
                self._send(500, {"message": "Internal Server Error"})
            else:
                self._send(status, {"message": message})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path != INGESTION_PATH:
                self._send(404, {"message": "Not found"})
                return
            body = self._body()
            batch = body.get("batch") if isinstance(body, dict) else None
            if not isinstance(batch, list) or not all(isinstance(e, dict) for e in batch):
                self._send(400, {"message": "batch must be a list of events"})
                return
            successes: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            created: list[str] = []
            for event in batch:
                if event.get("type") == TRACE_CREATE and isinstance(event.get("body"), dict):
                    created.append(str(event["body"].get("id")))
                    successes.append({"id": event.get("id"), "status": 201})
                else:
                    errors.append({"id": event.get("id"), "status": 400, "message": "unsupported"})
            stub.create_traces(created)
            self._send(207, {"successes": successes, "errors": errors})

    return Handler
