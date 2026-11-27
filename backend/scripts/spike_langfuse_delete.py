"""E12-T3 spike: how does Langfuse trace deletion behave, and how long until a trace is gone?

Run by hand, never in the PR gate, as a module from backend/ (not `python scripts/...`):

    cd backend && python -m scripts.spike_langfuse_delete

Demo mode (the default) runs the probe against the in-process stub in tests/stubs/langfuse_stub.py
in its three modes (honour, ignore, error) on a virtual clock, and labels the output
UNVERIFIED-pending-K2. Nothing is measured on Langfuse. Live mode needs the Langfuse Cloud keys
(K2):

    --mode live --action commands [--run-id R]    print the curl commands of the live run
    --mode live --action run [--run-id R] [--out F]  run the probe once against LANGFUSE_HOST

Live `run` reads LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY from the environment,
creates two throwaway traces named fa-spike-*, deletes one with the single call and one with the
batch call, and polls until GET returns 404. The findings and the reasoning are in
docs/adr/langfuse-delete-behaviour.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from tests.stubs.langfuse_stub import DEMO_PUBLIC_KEY, DEMO_SECRET_KEY, LangfuseStub

log = structlog.get_logger()

# The spike knobs: each has a default and an environment override. They are spike-local on purpose,
# the script is run by hand against a throwaway trace, so none of them goes into Settings.
ENV_TIMEOUT_S = "LANGFUSE_DELETE_TIMEOUT_S"
ENV_POLL_INTERVAL_S = "LANGFUSE_DELETE_POLL_INTERVAL_S"
ENV_POLL_TIMEOUT_S = "LANGFUSE_DELETE_POLL_TIMEOUT_S"
# Per HTTP request.
DEFAULT_TIMEOUT_S = 10.0
# ADR-030 says deletion is "usually within 15 minutes": poll often enough to see seconds.
DEFAULT_POLL_INTERVAL_S = 15.0
# Twice the 15 minutes of ADR-030, then the trace counts as "not honoured".
DEFAULT_POLL_TIMEOUT_S = 1800.0

# The live probe only ever touches traces it created itself, named with this prefix.
SPIKE_TRACE_PREFIX = "fa-spike-"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PLACEHOLDER = "REPLACE_ME"
TRACES_PATH = "/api/public/traces"
INGESTION_PATH = "/api/public/ingestion"
# The three modes of the demo, with an illustrative delete delay for honour (seconds).
DEMO_HONOUR_DELAY_S = 90.0
DEMO_MODES = (("honour", DEMO_HONOUR_DELAY_S), ("ignore", 0.0), ("error", 0.0))

HONOURED = "honoured"
IGNORED = "accepted but not honoured"
REJECTED = "delete rejected"
UNVERIFIED = "UNVERIFIED-pending-K2"


class SpikeError(Exception):
    """A probe step failed in a way the operator must see (bad setting, refused call, timeout)."""


@dataclass(frozen=True)
class ProbeSettings:
    timeout_s: float = DEFAULT_TIMEOUT_S
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S
    poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ProbeSettings:
        return cls(
            timeout_s=_positive(environ, ENV_TIMEOUT_S, DEFAULT_TIMEOUT_S),
            poll_interval_s=_positive(environ, ENV_POLL_INTERVAL_S, DEFAULT_POLL_INTERVAL_S),
            poll_timeout_s=_positive(environ, ENV_POLL_TIMEOUT_S, DEFAULT_POLL_TIMEOUT_S),
        )


def _positive(environ: Mapping[str, str], key: str, default: float) -> float:
    raw = environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SpikeError(f"{key} must be a number of seconds, got {raw!r}") from exc
    if value <= 0:
        raise SpikeError(f"{key} must be greater than 0, got {raw!r}")
    return value


class VirtualClock:
    """A clock that only moves when the probe sleeps, so the demo and the tests need no waiting."""

    def __init__(self) -> None:
        self._now = 0.0

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


@dataclass(frozen=True)
class Response:
    status: int
    body: Any

    @property
    def accepted(self) -> bool:
        return 200 <= self.status <= 299


class LangfuseClient:
    """The few public-API calls the probe needs, with one timeout for every request."""

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self._http = httpx.Client(
            base_url=host.rstrip("/"),
            auth=(public_key, secret_key),
            timeout=timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, body: Any = None) -> Response:
        try:
            resp = self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise SpikeError(f"{method} {path} failed: {exc!r}") from exc
        try:
            payload: Any = resp.json()
        except ValueError:
            payload = resp.text
        return Response(resp.status_code, payload)

    def ingest_trace(self, trace_id: str) -> Response:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        event = {
            "id": f"evt-{trace_id}",
            "type": "trace-create",
            "timestamp": stamp,
            "body": {"id": trace_id, "name": "fa-spike-delete"},
        }
        return self._call("POST", INGESTION_PATH, {"batch": [event]})

    def get_trace(self, trace_id: str) -> Response:
        return self._call("GET", f"{TRACES_PATH}/{trace_id}")

    def delete_trace(self, trace_id: str) -> Response:
        return self._call("DELETE", f"{TRACES_PATH}/{trace_id}")

    def delete_traces(self, trace_ids: list[str]) -> Response:
        return self._call("DELETE", TRACES_PATH, {"traceIds": trace_ids})


def poll_for_status(
    client: LangfuseClient,
    trace_id: str,
    want: int,
    settings: ProbeSettings,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    start: float | None = None,
) -> float | None:
    """Poll GET until it returns `want`; the seconds since `start`, or None at the poll timeout."""
    begin = clock() if start is None else start
    while True:
        status = client.get_trace(trace_id).status
        elapsed = clock() - begin
        if status == want:
            return elapsed
        if elapsed >= settings.poll_timeout_s:
            return None
        sleep(settings.poll_interval_s)


@dataclass(frozen=True)
class VariantResult:
    label: str
    trace_id: str
    delete_status: int
    delete_message: str | None
    get_after_delete_status: int
    seconds_to_gone: float | None
    repeat_delete_status: int | None


@dataclass(frozen=True)
class Verdict:
    verdict: str
    detail: str
    verified: bool
    label: str = ""

    def line(self) -> str:
        name = f"VERDICT[{self.label}]" if self.label else "VERDICT"
        text = f"{name}: {self.verdict} ({self.detail})"
        if not self.verified:
            text += f" [{UNVERIFIED}; DEMO STUB, not a measurement]"
        return text


def classify(result: VariantResult, verified: bool) -> Verdict:
    if not 200 <= result.delete_status <= 299:
        return Verdict(REJECTED, f"delete answered {result.delete_status}", verified, result.label)
    if result.seconds_to_gone is None:
        return Verdict(
            IGNORED,
            f"delete answered {result.delete_status}, GET still answered "
            f"{result.get_after_delete_status} when the polling ended",
            verified,
            result.label,
        )
    return Verdict(
        HONOURED,
        f"delete answered {result.delete_status}, GET answered "
        f"{result.get_after_delete_status} right after and 404 after {result.seconds_to_gone:g} s",
        verified,
        result.label,
    )


def _message(body: Any) -> str | None:
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return str(body["message"])
    return None


def run_probe(
    client: LangfuseClient,
    settings: ProbeSettings,
    run_id: str,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> list[VariantResult]:
    """One single-delete and one batch-delete of a throwaway trace, each followed to its 404."""
    results: list[VariantResult] = []
    for label in ("single", "batch"):
        trace_id = f"{SPIKE_TRACE_PREFIX}{label}-{run_id}"
        created = client.ingest_trace(trace_id)
        if not 200 <= created.status <= 299:
            raise SpikeError(f"ingestion of {trace_id} answered {created.status}")
        if poll_for_status(client, trace_id, 200, settings, clock, sleep) is None:
            raise SpikeError(f"{trace_id} never became visible within the poll timeout")
        start = clock()
        deleted = (
            client.delete_trace(trace_id) if label == "single" else client.delete_traces([trace_id])
        )
        after = client.get_trace(trace_id).status
        seconds: float | None = None
        repeat: int | None = None
        if deleted.accepted:
            seconds = poll_for_status(client, trace_id, 404, settings, clock, sleep, start=start)
            if seconds is not None:
                repeat = client.delete_trace(trace_id).status
        results.append(
            VariantResult(
                label=label,
                trace_id=trace_id,
                delete_status=deleted.status,
                delete_message=_message(deleted.body),
                get_after_delete_status=after,
                seconds_to_gone=seconds,
                repeat_delete_status=repeat,
            )
        )
    return results


def live_commands(run_id: str) -> list[str]:
    """The curl commands of the live run, in order. Comment lines start with '#'."""
    host = '"$LANGFUSE_HOST'
    auth = '-u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY"'
    code = "-w '\\n%{http_code}\\n'"
    json_header = "-H 'Content-Type: application/json'"
    commands: list[str] = []
    for label in ("single", "batch"):
        tid = f"{SPIKE_TRACE_PREFIX}{label}-{run_id}"
        event = {
            "id": f"evt-{tid}",
            "type": "trace-create",
            "timestamp": "<NOW-ISO-8601>",
            "body": {"id": tid, "name": "fa-spike-delete"},
        }
        ingest_body = json.dumps({"batch": [event]})
        delete_cmd = (
            f'curl -sS {auth} {code} -X DELETE {host}{TRACES_PATH}/{tid}"'
            if label == "single"
            else f'curl -sS {auth} {code} -X DELETE {host}{TRACES_PATH}" {json_header} '
            f"-d '{json.dumps({'traceIds': [tid]})}'"
        )
        get_cmd = f'curl -sS {auth} {code} {host}{TRACES_PATH}/{tid}"'
        commands += [
            f"# {label}: create the throwaway trace",
            f'curl -sS {auth} {code} -X POST {host}{INGESTION_PATH}" {json_header} '
            f"-d '{ingest_body}'",
            f"# {label}: the trace must answer 200 before the delete",
            get_cmd,
            f"# {label}: delete it (note the status code and the message), note the time",
            delete_cmd,
            f"# {label}: GET right after the delete (note 200 or 404)",
            get_cmd,
            f"# {label}: repeat the GET every {int(DEFAULT_POLL_INTERVAL_S)} s, "
            "note the seconds until it answers 404",
            get_cmd,
            f"# {label}: delete the same id again, to see the status of a missing trace",
            f'curl -sS {auth} {code} -X DELETE {host}{TRACES_PATH}/{tid}"',
        ]
    return commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Langfuse trace delete probe (E12-T3 spike)")
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--action", choices=("commands", "run"), default="commands")
    parser.add_argument("--run-id", default=None, help="live: suffix of the throwaway trace ids")
    parser.add_argument("--out", type=Path, default=None, help="live run: write the result JSON")
    return parser


def run_demo(settings: ProbeSettings) -> int:
    report: dict[str, Any] = {"demo": True, "modes": {}}
    lines: list[str] = []
    for mode, delay in DEMO_MODES:
        vclock = VirtualClock()
        with LangfuseStub(
            mode, delete_delay_s=delay, error_count=1, clock=vclock.now, environ={}
        ) as stub:
            client = LangfuseClient(stub.url, DEMO_PUBLIC_KEY, DEMO_SECRET_KEY, settings.timeout_s)
            try:
                results = run_probe(client, settings, "demo", vclock.now, vclock.sleep)
            finally:
                client.close()
        report["modes"][mode] = [asdict(r) for r in results]
        for result in results:
            verdict = classify(result, verified=False)
            lines.append(replace(verdict, label=f"{mode}/{result.label}").line())
    sys.stdout.write(json.dumps(report, indent=2) + "\n" + "\n".join(lines) + "\n")
    return 0


def run_live(args: argparse.Namespace, environ: Mapping[str, str], settings: ProbeSettings) -> int:
    run_id = args.run_id or uuid.uuid4().hex[:8]
    if not RUN_ID_PATTERN.match(run_id):
        raise SpikeError(f"--run-id must match {RUN_ID_PATTERN.pattern}, got {run_id!r}")
    if args.action == "commands":
        sys.stdout.write("\n".join(live_commands(run_id)) + "\n")
        return 0
    host = environ.get("LANGFUSE_HOST", "")
    public_key = environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = environ.get("LANGFUSE_SECRET_KEY", "")
    for name, value in (
        ("LANGFUSE_HOST", host),
        ("LANGFUSE_PUBLIC_KEY", public_key),
        ("LANGFUSE_SECRET_KEY", secret_key),
    ):
        if not value or value == PLACEHOLDER:
            raise SpikeError(f"{name} is not set to a real value (K2)")
    client = LangfuseClient(host, public_key, secret_key, settings.timeout_s)
    try:
        results = run_probe(client, settings, run_id, time.monotonic, time.sleep)
    finally:
        client.close()
    report = {"demo": False, "run_id": run_id, "variants": [asdict(r) for r in results]}
    text = json.dumps(report, indent=2)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    for result in results:
        sys.stdout.write(classify(result, verified=True).line() + "\n")
    return 0


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    try:
        settings = ProbeSettings.from_env(env)
        if args.mode == "demo":
            return run_demo(settings)
        return run_live(args, env, settings)
    except SpikeError as exc:
        log.error("spike_failed", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
