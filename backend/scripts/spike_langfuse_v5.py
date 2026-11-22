"""E0-T1 spike: prove Langfuse Python SDK 5.x exports masked traces to Langfuse Cloud.

Creates one trace with
  (a) a normal span whose input holds a marker, exported through `mask_otel_spans`
      (the marker must arrive as "[REDACTED]"), and
  (b) a sensitive span created with `capture_input=False, capture_output=False`.
It flushes, then polls GET {host}/api/public/traces/{id} (basic auth) and checks the result.

Two modes, one of them required:
  --demo  needs no account: the SDK exports to an in-process OTLP/HTTP receiver on 127.0.0.1 with
          the demo keys below, and the captured spans are checked (masking function ran before
          export, no sensitive value in any exported byte).
  --live  the real run against Langfuse Cloud. Reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY,
          LANGFUSE_HOST from the environment (the typed Settings keys arrive in E0-T3/E0-T8).

Key values are never printed or logged.

Exit codes: 0 pass, 1 check failed (leak or missing trace data), 2 auth, transport or config error.

Usage: poetry run python scripts/spike_langfuse_v5.py (--demo | --live) [--no-mask]
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any

# Spike knobs (a throwaway script: the app's Settings class does not carry them).
MARKER = "SPIKE-SSN-123-45-6789"
MARKER_PREFIX = "SPIKE-SSN"
REDACTED = "[REDACTED]"
NORMAL_SPAN = "spike-normal"
SENSITIVE_SPAN = "spike-sensitive"
ROOT_SPAN = "spike-root"
DEFAULT_POLL_TIMEOUT_S = 60.0
DEFAULT_POLL_INTERVAL_S = 3.0
DEFAULT_HTTP_TIMEOUT_S = 15.0

# Demo mode knobs: fixed non-secret stand-ins, and a receiver bound to loopback only.
DEMO_PUBLIC_KEY = "demo-public-key"
DEMO_SECRET_KEY = "demo-secret-key"
DEMO_BIND_HOST = "127.0.0.1"
DEMO_BIND_PORT = 0  # 0 = the OS picks a free port, so two runs never clash
DEMO_POLL_INTERVAL_S = 0.05  # how fast the receiver notices its shutdown request
OTLP_TRACES_PATH = "/api/public/otel/v1/traces"

# Attribute keys the 5.1.0 SDK writes span input/output to (langfuse/_client/attributes.py:42-43).
INPUT_KEY = "langfuse.observation.input"
OUTPUT_KEY = "langfuse.observation.output"

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_ERROR = 2


class SpikeError(Exception):
    """Auth, transport or configuration failure (exit code 2)."""


def redact_marker(value: object) -> object:
    """Replace the marker inside a string attribute value; other values pass through."""
    if isinstance(value, str) and MARKER_PREFIX in value:
        return value.replace(MARKER, REDACTED)
    return value


def mask_otel_spans(*, params: Any) -> Any:
    """Export-stage masking hook passed to `Langfuse(mask_otel_spans=...)`.

    Patches only the observation input/output attributes that contain the marker.
    """
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches: dict[Any, Any] = {}
    for identifier, span in params.spans.items():
        updates = {
            key: masked
            for key in (INPUT_KEY, OUTPUT_KEY)
            if key in span.attributes
            for masked in [redact_marker(span.attributes[key])]
            if masked != span.attributes[key]
        }
        if updates:
            patches[identifier] = OtelSpanPatch(set_attributes=updates)
    return MaskOtelSpansResult(span_patches=patches)


def _has_content(value: object) -> bool:
    return value not in (None, "", [], {})


def _leaks_marker(value: object) -> bool:
    return MARKER_PREFIX in json.dumps(value, default=str)


def evaluate_trace(trace: Mapping[str, Any]) -> list[str]:
    """Return the reasons a fetched trace fails the spike (empty list means pass)."""
    problems: list[str] = []
    observations = list(trace.get("observations") or [])
    names = {obs.get("name") for obs in observations}
    for expected in (NORMAL_SPAN, SENSITIVE_SPAN):
        if expected not in names:
            problems.append(f"observation '{expected}' missing from trace")
    for obs in observations:
        for field in ("input", "output"):
            if _leaks_marker(obs.get(field)):
                name = obs.get("name")
                problems.append(f"{MARKER_PREFIX} found in {field} of observation '{name}'")
        if obs.get("name") == SENSITIVE_SPAN:
            for field in ("input", "output"):
                if _has_content(obs.get(field)):
                    problems.append(f"sensitive span has non-empty {field}")
    for field in ("input", "output"):
        if _leaks_marker(trace.get(field)):
            problems.append(f"{MARKER_PREFIX} found in trace {field}")
    return problems


def is_complete(trace: Mapping[str, Any]) -> bool:
    """True once both spike observations have been ingested."""
    names = {obs.get("name") for obs in trace.get("observations") or []}
    return {NORMAL_SPAN, SENSITIVE_SPAN} <= names


def fetch_trace(
    host: str, public_key: str, secret_key: str, trace_id: str, timeout: float
) -> dict[str, Any] | None:
    """GET one trace. None means not found yet (404). Auth and transport errors raise SpikeError."""
    url = f"{host.rstrip('/')}/api/public/traces/{urllib.parse.quote(trace_id, safe='')}"
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise SpikeError("LANGFUSE_HOST must start with http:// or https://")
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    request = urllib.request.Request(  # noqa: S310 (scheme checked above)
        url, headers={"Authorization": f"Basic {token}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (401, 403):
            raise SpikeError(f"Langfuse API rejected the key pair (HTTP {exc.code})") from exc
        raise SpikeError(f"Langfuse API error (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SpikeError(f"transport error: {type(exc).__name__}") from exc
    if not isinstance(body, dict):
        raise SpikeError("unexpected response body from Langfuse API")
    return body


def poll_trace(
    fetch: Callable[[], dict[str, Any] | None],
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Poll until the trace is complete or the timeout passes; return the last trace seen.

    Auth errors raise at once; a transport error is retried until the deadline, then raised.
    """
    deadline = clock() + timeout
    last: dict[str, Any] | None = None
    last_transport: SpikeError | None = None
    while True:
        try:
            trace = fetch()
            last_transport = None
        except SpikeError as exc:
            if "rejected the key pair" in str(exc):
                raise
            last_transport = exc
            trace = None
        if trace is not None:
            last = trace
            if is_complete(trace):
                return trace
        if clock() >= deadline:
            if last_transport is not None and last is None:
                raise last_transport
            return last
        sleep(interval)


def run_spike(
    host: str,
    public_key: str,
    secret_key: str,
    *,
    mask: bool,
    mask_fn: Callable[..., Any] | None = None,
    tracer_provider: Any = None,
) -> str:
    """Create the spike trace through the SDK and return its trace id.

    `mask_fn` replaces `mask_otel_spans` as the export hook (the demo passes a recording wrapper).
    `tracer_provider` keeps the run off the process-wide OpenTelemetry provider (the SDK otherwise
    attaches every client's exporter to it, so a second client in one process also feeds the first).
    """
    from langfuse import Langfuse, observe

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=host,
        mask_otel_spans=(mask_fn or mask_otel_spans) if mask else None,
        tracer_provider=tracer_provider,
    )
    trace_ids: list[str] = []

    @observe(name=NORMAL_SPAN)
    def normal(text: str) -> str:
        return f"echo: {text}"

    @observe(name=SENSITIVE_SPAN, capture_input=False, capture_output=False)
    def sensitive(text: str) -> str:
        return f"echo: {text}"

    @observe(name=ROOT_SPAN, capture_input=False, capture_output=False)
    def root() -> None:
        trace_id = client.get_current_trace_id()
        if trace_id:
            trace_ids.append(trace_id)
        normal(f"customer ssn {MARKER}")
        sensitive(f"customer ssn {MARKER}")

    root()
    client.flush()
    if not trace_ids:
        raise SpikeError("SDK produced no trace id")
    return trace_ids[0]


class OtlpSink:
    """In-process OTLP/HTTP receiver: keeps the raw body of every trace export it receives."""

    def __init__(self, bind_host: str = DEMO_BIND_HOST, port: int = DEMO_BIND_PORT) -> None:
        self.bodies: list[bytes] = []
        self._lock = threading.Lock()
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (http.server API)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                if self.path == OTLP_TRACES_PATH:
                    sink.record(raw)
                    content_type, reply = "application/x-protobuf", b""
                else:
                    content_type, reply = "application/json", b"{}"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer((bind_host, port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": DEMO_POLL_INTERVAL_S},
            daemon=True,
        )

    def record(self, body: bytes) -> None:
        with self._lock:
            self.bodies.append(body)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def __enter__(self) -> OtlpSink:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


def spans_from_bodies(bodies: list[bytes]) -> list[dict[str, Any]]:
    """Decode OTLP export requests into {name, attributes} dicts (string attributes only)."""
    from google.protobuf.message import DecodeError
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    spans: list[dict[str, Any]] = []
    for body in bodies:
        request = ExportTraceServiceRequest()
        try:
            request.ParseFromString(body)
        except DecodeError as exc:
            raise SpikeError("captured export is not a valid OTLP request") from exc
        for resource_spans in request.resource_spans:
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    attributes = {
                        kv.key: kv.value.string_value
                        for kv in span.attributes
                        if kv.value.HasField("string_value")
                    }
                    spans.append({"name": span.name, "attributes": attributes})
    return spans


def trace_from_spans(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape captured spans like the Langfuse trace API body so `evaluate_trace` applies."""
    return {
        "input": None,
        "output": None,
        "observations": [
            {
                "name": span["name"],
                "input": span["attributes"].get(INPUT_KEY),
                "output": span["attributes"].get(OUTPUT_KEY),
            }
            for span in spans
        ],
    }


def evaluate_capture(bodies: list[bytes], mask_calls: list[int], *, mask: bool) -> list[str]:
    """Return the reasons a local capture fails the demo check (empty list means pass)."""
    problems = evaluate_trace(trace_from_spans(spans_from_bodies(bodies)))
    if any(MARKER_PREFIX.encode() in body for body in bodies):
        problems.append(f"{MARKER_PREFIX} found in the raw exported payload")
    if mask and not mask_calls:
        problems.append("masking function never ran before export")
    return problems


def reset_langfuse_clients() -> None:
    """Drop the SDK's per-public-key client singletons so a later run builds a fresh client."""
    from langfuse._client.resource_manager import LangfuseResourceManager

    LangfuseResourceManager.reset()


def run_demo(*, mask: bool) -> list[str]:
    """Run the spike against a local OTLP receiver with demo keys; return the problems found."""
    from opentelemetry.sdk.trace import TracerProvider

    mask_calls: list[int] = []

    def recording_mask(*, params: Any) -> Any:
        mask_calls.append(len(params.spans))
        return mask_otel_spans(params=params)

    with OtlpSink() as sink:
        try:
            run_spike(
                sink.url,
                DEMO_PUBLIC_KEY,
                DEMO_SECRET_KEY,
                mask=mask,
                mask_fn=recording_mask,
                tracer_provider=TracerProvider(),
            )
        finally:
            reset_langfuse_clients()
        bodies = list(sink.bodies)
    return evaluate_capture(bodies, mask_calls, mask=mask)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="local OTLP capture, no account needed")
    mode.add_argument("--live", action="store_true", help="real run against Langfuse Cloud")
    parser.add_argument("--no-mask", action="store_true", help="skip mask_otel_spans (must fail)")
    parser.add_argument("--poll-timeout", type=float, default=DEFAULT_POLL_TIMEOUT_S)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_S)
    parser.add_argument("--http-timeout", type=float, default=DEFAULT_HTTP_TIMEOUT_S)
    return parser.parse_args(argv)


def read_credentials(env: Mapping[str, str]) -> tuple[str, str, str]:
    names = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise SpikeError(f"missing environment variables: {', '.join(missing)}")
    return env["LANGFUSE_HOST"], env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.demo:
        try:
            problems = run_demo(mask=not args.no_mask)
        except SpikeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_ERROR
        for problem in problems:
            print(f"FAIL: {problem}")
        if problems:
            return EXIT_CHECK_FAILED
        print("PASS (demo: local OTLP capture): no sensitive value reached the exported spans")
        return EXIT_OK
    try:
        host, public_key, secret_key = read_credentials(os.environ)
        trace_id = run_spike(host, public_key, secret_key, mask=not args.no_mask)
        trace = poll_trace(
            lambda: fetch_trace(host, public_key, secret_key, trace_id, args.http_timeout),
            timeout=args.poll_timeout,
            interval=args.poll_interval,
        )
    except SpikeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if trace is None:
        print(f"FAIL: trace {trace_id} not retrievable within {args.poll_timeout:.0f}s")
        return EXIT_CHECK_FAILED
    problems = evaluate_trace(trace)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return EXIT_CHECK_FAILED
    print(f"PASS: trace {trace_id} fetched from {host.rstrip('/')}/api/public/traces/{trace_id}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
