"""E11-T1 spike: probe the FCA Handbook API for response format, rate behaviour and error codes.

Run by hand, never in the PR gate. It must run as a module from backend/ (not
`python scripts/spike_fca_api.py`), so `src` imports:

    cd backend && python -m scripts.spike_fca_api

Demo mode (the default, FCA_API_MODE=demo, key `demo-fca-key`) needs no account and no network: an
in-process transport replays the demo fixtures in tests/fixtures/fca_api/. Live mode needs a real
account (K7):

    cd backend && FCA_API_MODE=live FCA_API_KEY=<account key> python -m scripts.spike_fca_api \\
        --base-url <API base URL from the Handbook API page> \\
        --out-dir tests/fixtures/fca_api/recorded

Live sends 4 fixed requests plus a burst of FCA_API_BURST_REQUESTS (demo: 5 fixed), at most
FCA_API_RATE_LIMIT_RPS per second (a higher --rps is refused unless --allow-above-limit is given),
with a descriptive User-Agent, and only to an fca.org.uk host unless --allow-any-host is given. The
key is sent only in the auth header and is never printed or logged. The findings are written up in
docs/spikes/fca-handbook-api.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import httpx
import structlog
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config import PLACEHOLDER_SECRETS, Settings

Mode = Literal["demo", "live"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "fca_api"
DEFAULT_LIVE_OUT_DIR = FIXTURES_DIR / "recorded"

DEMO_FCA_API_KEY = "demo-fca-key"  # not a secret: demo mode only, live mode refuses it


class FcaSpikeSettings(BaseSettings):
    """Spike-local knobs, read from FCA_API_* environment variables.

    Spike-local on purpose: this script is run by hand, once. The connector (E11-T20) moves the
    mode, key, timeout, burst and base URL into its own config, so app boot never depends on
    them. The rate stays in the global Settings (FCA_API_RATE_LIMIT_RPS); this reads its default.
    """

    model_config = SettingsConfigDict(
        env_prefix="FCA_API_", extra="ignore", hide_input_in_errors=True
    )

    mode: Mode = "demo"
    key: SecretStr = SecretStr(DEMO_FCA_API_KEY)
    timeout_s: float = Field(default=30.0, gt=0)
    # Repeat reads of the first module after the fixed probes, to look for a limit at the rate.
    burst_requests: int = Field(default=10, gt=0)
    # Past-date probe: this many days before today (docs: only current and future versions).
    past_date_offset_days: int = Field(default=30, gt=0)
    base_url: str | None = None
    rate_limit_rps: float = Field(
        default=Settings.model_fields["fca_api_rate_limit_rps"].default,
        gt=0,
        validation_alias="FCA_API_RATE_LIMIT_RPS",
    )

    @model_validator(mode="after")
    def _check_key(self) -> FcaSpikeSettings:
        key = self.key.get_secret_value().strip()
        if not key:
            raise ValueError("FCA_API_KEY must not be empty")
        if key.lower() in PLACEHOLDER_SECRETS:
            raise ValueError("FCA_API_KEY is still a placeholder; set a real value")
        if self.mode == "live" and key == DEMO_FCA_API_KEY:
            raise ValueError(
                "FCA_API_KEY is the demo key; FCA_API_MODE=live needs the key of a real "
                "FCA Handbook API account"
            )
        return self


# Everything below is spike-local too: the FCA does not publish its endpoint paths, auth header or
# field names where they can be read without a login (docs/spikes/fca-handbook-api.md).
DEMO_BASE_URL = "https://fca-handbook-demo.invalid"  # RFC 2606 .invalid: never resolves
# ASSUMPTION: path and date parameter are not published; --module-path overrides the path.
MODULE_PATH_DEFAULT = "/handbook/{sourcebook}/{chapter}"
DATE_PARAM = "date"
# ASSUMPTION: the auth header name is not published; --auth-header overrides it.
AUTH_HEADER_DEFAULT = "X-Api-Key"
# ASSUMPTION: the API lives on an fca.org.uk host (the Handbook API page is on handbook.fca.org.uk).
FCA_HOST_SUFFIX = "fca.org.uk"
USER_AGENT = "FinAdvisor-spike/0.1 (FCA Handbook API probe; research prototype)"
MODULES: tuple[tuple[str, str], ...] = (("COBS", "9A"), ("COBS", "6"))
# Keys that stand in for a revoked and a suspended login. Demo answers 401 and 403. A live API
# cannot be made to revoke a login, so live sends one unknown key and labels the row as such.
DEMO_REVOKED_KEY = "demo-revoked-key"
DEMO_SUSPENDED_KEY = "demo-suspended-key"
UNKNOWN_KEY = "unknown-key-probe"
UNKNOWN_KEY_LABEL = "unknown-key response (no revoked key available)"
DEMO_SOURCE = "demo fixture, from docs"
DEMO_MARKER = "_demo"
# Response headers that could carry a vendor limit; recorded when present.
RATE_HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "retry-after")
DEMO_LAST_MODIFIED_HEADER = "Thu, 01 Oct 2026 00:00:00 GMT"


class SampleError(ValueError):
    """A fixture file or response does not have the expected shape."""


class Provision(BaseModel):
    reference: str
    type: str
    text: str


class ModuleResponse(BaseModel):
    """Assumed shape of one module response (demo fixture, from docs; confirm with K7)."""

    reference: str
    title: str
    version_date: date
    last_modified: date
    provisions: list[Provision]


@dataclass(frozen=True)
class Probe:
    label: str
    path: str
    status: int
    content_type: str | None
    rate_headers: dict[str, str]


class Throttle:
    """Blocks so that calls are at least 1/rps seconds apart. Clock and sleep are injectable."""

    def __init__(
        self,
        rps: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rps <= 0:
            raise ValueError("rps must be greater than 0")
        self.rps = rps
        self._interval = 1.0 / rps
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    async def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self._interval - (now - self._last)
            if remaining > 0:
                await self._sleep(remaining)
                now = self._clock()
        self._last = now


def load_sample(name: str, directory: Path = FIXTURES_DIR) -> dict[str, Any]:
    """Load one demo fixture; refuses a file that is not marked as a demo fixture."""
    payload = json.loads((directory / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SampleError(f"{name}: expected a JSON object")
    if DEMO_MARKER not in payload:
        raise SampleError(f"{name}: missing the {DEMO_MARKER} demo marker")
    return cast("dict[str, Any]", payload)


def parse_module(payload: Mapping[str, Any]) -> ModuleResponse:
    try:
        return ModuleResponse.model_validate(payload)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        raise SampleError(f"module response does not parse: {fields}") from exc


def load_settings() -> FcaSpikeSettings:
    """FcaSpikeSettings from the environment; a bad value raises ValueError naming its variable."""
    try:
        return FcaSpikeSettings()
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            loc = str(error["loc"][0]) if error["loc"] else ""
            field = FcaSpikeSettings.model_fields.get(loc)
            if field is not None and field.validation_alias is None:
                loc = f"FCA_API_{loc.upper()}"
            problems.append(f"{loc}: {error['msg']}" if loc else str(error["msg"]))
        raise ValueError("; ".join(problems)) from exc


def resolve_rps(requested: float | None, configured: float, allow_above_limit: bool) -> float:
    """The rate to use: the configured one, or a lower request; higher only when forced."""
    if requested is None:
        return configured
    if requested > configured and not allow_above_limit:
        raise ValueError(
            f"--rps {requested} is above the configured limit {configured} "
            "(FCA_API_RATE_LIMIT_RPS); pass --allow-above-limit to override"
        )
    return requested


def host_allowed(host: str) -> bool:
    return host == FCA_HOST_SUFFIX or host.endswith(f".{FCA_HOST_SUFFIX}")


def validate_base_url(mode: Mode, base_url: str | None, allow_any_host: bool) -> None:
    """Refuse a live target that is missing, not https, carries credentials or is not FCA's."""
    if mode != "live":
        return
    if not base_url:
        raise ValueError("live mode needs --base-url (the API base URL from your account)")
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
    except ValueError:
        raise ValueError("--base-url is not a valid URL") from None
    if parts.scheme != "https" or not host:
        raise ValueError("--base-url must be an absolute https URL (the key is sent to it)")
    if parts.username is not None or parts.password is not None:
        raise ValueError("--base-url must not contain credentials (userinfo)")
    if not allow_any_host and not host_allowed(host):
        raise ValueError(
            f"--base-url host {host!r} is not an {FCA_HOST_SUFFIX} host; "
            "pass --allow-any-host to send the key there anyway"
        )


def build_demo_transport(auth_header: str, today: date) -> httpx.MockTransport:
    """In-process stand-in for the API that replays the demo fixtures. No network."""
    module_re = re.compile(r"^/handbook/COBS/(9A|6)$")
    modules = {"9A": load_sample("cobs_9a_module.json"), "6": load_sample("cobs_6_module.json")}
    past = load_sample("past_date_error.json")
    unauthorized = load_sample("auth_error_401.json")
    forbidden = load_sample("auth_error_403.json")

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get(auth_header)
        if key == DEMO_SUSPENDED_KEY:
            return httpx.Response(403, json=forbidden)
        if key != DEMO_FCA_API_KEY:
            return httpx.Response(401, json=unauthorized)
        match = module_re.match(request.url.path)
        if match is None:
            return httpx.Response(404, json={"error": "not_found"})
        requested = request.url.params.get(DATE_PARAM)
        if requested is not None and date.fromisoformat(requested) < today:
            return httpx.Response(400, json=past)
        return httpx.Response(
            200, json=modules[match.group(1)], headers={"Last-Modified": DEMO_LAST_MODIFIED_HEADER}
        )

    return httpx.MockTransport(handler)


def make_client(
    transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 30.0
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, timeout=timeout_s)


def rate_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower().startswith(RATE_HEADER_PREFIXES)}


def body_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return cast("dict[str, Any]", body) if isinstance(body, dict) else None


def describe_format(response: httpx.Response) -> dict[str, Any]:
    """Format facts of a module response. A shape that does not parse is recorded, not raised."""
    body = body_json(response)
    parsed = False
    last_modified = None
    if body is not None:
        try:
            last_modified = parse_module(body).last_modified.isoformat()
            parsed = True
        except SampleError:
            parsed = False
    return {
        "content_type": response.headers.get("content-type"),
        "parsed": parsed,
        "top_level_keys": sorted(body) if body is not None else [],
        "last_modified": last_modified,
    }


def rate_statement(
    requests: int, rps: float, first_429: int | None, retry_after: str | None
) -> str:
    if first_429 is None:
        return f"none reached after {requests} requests at {rps} rps"
    suffix = f" (Retry-After {retry_after})" if retry_after else ""
    return f"limited at request {first_429} of the burst at {rps} rps{suffix}"


def write_sample(out_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    target.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def stderr_logger() -> Any:
    """Logger on the current stderr, so stdout stays the JSON report. The key is never logged."""
    return structlog.wrap_logger(structlog.PrintLogger(sys.stderr))


async def run(
    client: httpx.AsyncClient,
    throttle: Throttle,
    settings: FcaSpikeSettings,
    *,
    base_url: str,
    auth_header: str,
    module_path: str,
    today: date,
    out_dir: Path | None,
) -> dict[str, Any]:
    """Probe and report. Probes record raw statuses, so there are no retries on purpose."""
    probes: list[Probe] = []
    log = stderr_logger()
    key = settings.key.get_secret_value()

    async def fetch(
        label: str, sourcebook: str, chapter: str, params: dict[str, str], use_key: str
    ) -> httpx.Response:
        await throttle.wait()
        path = module_path.format(sourcebook=sourcebook, chapter=chapter)
        response = await client.get(
            f"{base_url}{path}",
            params=params,
            headers={"User-Agent": USER_AGENT, auth_header: use_key},
        )
        probe = Probe(
            label=label,
            path=path,
            status=response.status_code,
            content_type=response.headers.get("content-type"),
            rate_headers=rate_headers(response.headers),
        )
        probes.append(probe)
        log.info("probe", **probe.__dict__)
        return response

    first: httpx.Response | None = None
    for sourcebook, chapter in MODULES:
        response = await fetch(f"{sourcebook} {chapter} current", sourcebook, chapter, {}, key)
        if first is None:
            first = response
        body = body_json(response)
        if out_dir is not None and response.status_code == 200 and body is not None:
            write_sample(out_dir, f"{sourcebook.lower()}_{chapter.lower()}_module.json", body)
    if first is None:
        raise ValueError("MODULES is empty")
    sourcebook, chapter = MODULES[0]

    offset = timedelta(days=settings.past_date_offset_days)
    past_date = (today - offset).isoformat()
    past = await fetch("past date", sourcebook, chapter, {DATE_PARAM: past_date}, key)
    auth: dict[str, int] = {}
    if settings.mode == "demo":
        revoked = await fetch("revoked login", sourcebook, chapter, {}, DEMO_REVOKED_KEY)
        suspended = await fetch("suspended login", sourcebook, chapter, {}, DEMO_SUSPENDED_KEY)
        auth = {"revoked": revoked.status_code, "suspended": suspended.status_code}
    else:
        unknown = await fetch(UNKNOWN_KEY_LABEL, sourcebook, chapter, {}, UNKNOWN_KEY)
        auth = {"unknown_key": unknown.status_code}

    burst = 0
    first_429: int | None = None
    retry_after: str | None = None
    for index in range(1, settings.burst_requests + 1):
        response = await fetch("burst", sourcebook, chapter, {}, key)
        burst = index
        if response.status_code == 429:
            first_429 = index
            retry_after = response.headers.get("retry-after")
            break

    return {
        "mode": settings.mode,
        "demo": settings.mode == "demo",
        "source": DEMO_SOURCE if settings.mode == "demo" else "live",
        "requests": len(probes),
        "rps": throttle.rps,
        "probes": [p.__dict__ for p in probes],
        "format": describe_format(first),
        "last_modified_header": first.headers.get("last-modified"),
        "past_date": {"date": past_date, "status": past.status_code},
        "auth": auth,
        "rate": {
            "requests": burst,
            "rps": throttle.rps,
            "limited": first_429 is not None,
            "first_429_at": first_429,
            "retry_after": retry_after,
            "statement": rate_statement(burst, throttle.rps, first_429, retry_after),
        },
    }


async def no_sleep(_seconds: float) -> None:
    """Demo mode never touches a network, so it does not wait between requests."""


async def amain(
    argv: list[str] | None = None, live_transport: httpx.AsyncBaseTransport | None = None
) -> int:
    parser = argparse.ArgumentParser(description="Probe the FCA Handbook API (E11-T1 spike)")
    parser.add_argument(
        "--base-url", default=None, help="API base URL (live mode only; or FCA_API_BASE_URL)"
    )
    parser.add_argument("--auth-header", default=AUTH_HEADER_DEFAULT)
    parser.add_argument("--module-path", default=MODULE_PATH_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_LIVE_OUT_DIR)
    parser.add_argument(
        "--rps",
        type=float,
        default=None,
        help="requests per second (default and maximum: FCA_API_RATE_LIMIT_RPS, default 1.0)",
    )
    parser.add_argument(
        "--allow-above-limit", action="store_true", help="allow --rps above the configured limit"
    )
    parser.add_argument(
        "--allow-any-host",
        action="store_true",
        help=f"allow a live --base-url that is not an {FCA_HOST_SUFFIX} host",
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        rps = resolve_rps(args.rps, settings.rate_limit_rps, args.allow_above_limit)
        live_url = args.base_url or settings.base_url
        validate_base_url(settings.mode, live_url, args.allow_any_host)
    except ValueError as exc:
        parser.error(str(exc))

    today = date.today()
    if settings.mode == "demo":
        transport: httpx.AsyncBaseTransport | None = build_demo_transport(args.auth_header, today)
        throttle = Throttle(rps, sleep=no_sleep)
        base_url, out_dir = DEMO_BASE_URL, None
    else:
        transport, throttle = live_transport, Throttle(rps)
        base_url, out_dir = cast("str", live_url), args.out_dir
    try:
        async with make_client(transport, settings.timeout_s) as client:
            report = await run(
                client,
                throttle,
                settings,
                base_url=base_url,
                auth_header=args.auth_header,
                module_path=args.module_path,
                today=today,
                out_dir=out_dir,
            )
    except httpx.HTTPError as exc:
        stderr_logger().error("transport_error", error=type(exc).__name__)
        return 1
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    sys.exit(main())
