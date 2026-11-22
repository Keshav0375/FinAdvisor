"""E11-T3 spike: probe the eCFR versioner API and record samples.

Run by hand, never in the PR gate:

    cd backend && python -m scripts.spike_ecfr_api --out-dir tests/fixtures/ecfr

Makes a handful of requests (7 by default), at most CONNECTOR_RATE_RPS_ECFR per second, with a
descriptive User-Agent and no personal data. The findings are written up in docs/spikes/ecfr-api.md.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

from src.config import Settings

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "ecfr"
BASE_URL = "https://www.ecfr.gov/api/versioner/v1"
USER_AGENT = "FinAdvisor-spike/0.1 (eCFR API probe; research prototype)"
TITLE = 17
PART = 275
TIMEOUT_S = 30.0
# `None` means the Accept-Encoding header is not set at all on the request.
ENCODING_VARIANTS: tuple[tuple[str, str | None], ...] = (
    ("gzip", "gzip"),
    ("identity", "identity"),
    ("none", None),
)
# Response headers that could carry a vendor limit; recorded when present.
RATE_HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "retry-after")
SECTION_RE = re.compile(r'TYPE="SECTION"')


@dataclass(frozen=True)
class Probe:
    label: str
    path: str
    encoding: str
    status: int
    content_encoding: str | None
    body_bytes: int
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


def default_rps() -> float:
    """The knob's default from Settings, read without building Settings (no secrets needed)."""
    default = Settings.model_fields["ecfr_rate_limit_rps"].default
    return float(default)


def make_client() -> httpx.AsyncClient:
    """Client without httpx's default Accept-Encoding, so each probe sends exactly what it sets."""
    client = httpx.AsyncClient()
    client.headers.pop("Accept-Encoding", None)
    return client


def build_headers(accept_encoding: str | None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if accept_encoding is not None:
        headers["Accept-Encoding"] = accept_encoding
    return headers


def rate_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower().startswith(RATE_HEADER_PREFIXES)}


def summarise_versions(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload["content_versions"]
    dates = sorted(r["amendment_date"] for r in records)
    return {
        "records": len(records),
        "removed": sum(1 for r in records if r["removed"]),
        "non_substantive": sum(1 for r in records if not r["substantive"]),
        "first_amendment_date": dates[0] if dates else None,
        "last_amendment_date": dates[-1] if dates else None,
        "fields": sorted({key for r in records for key in r}),
    }


def title_entry(payload: dict[str, Any], number: int) -> dict[str, Any]:
    for entry in payload["titles"]:
        if entry["number"] == number:
            return entry  # type: ignore[no-any-return]
    raise KeyError(f"title {number} not in titles.json")


def count_sections(xml_text: str) -> int:
    return len(SECTION_RE.findall(xml_text))


async def fetch(
    client: httpx.AsyncClient,
    throttle: Throttle,
    label: str,
    path: str,
    params: dict[str, str],
    accept_encoding: str | None,
) -> tuple[Probe, httpx.Response]:
    await throttle.wait()
    response = await client.get(
        f"{BASE_URL}/{path}",
        params=params,
        headers=build_headers(accept_encoding),
        timeout=TIMEOUT_S,
    )
    probe = Probe(
        label=label,
        path=path,
        encoding=accept_encoding or "(header omitted)",
        status=response.status_code,
        content_encoding=response.headers.get("content-encoding"),
        body_bytes=len(response.content),
        rate_headers=rate_headers(response.headers),
    )
    log.info("probe", **probe.__dict__)
    return probe, response


def write_sample(out_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    target.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


async def run(client: httpx.AsyncClient, throttle: Throttle, out_dir: Path) -> dict[str, Any]:
    probes: list[Probe] = []
    samples: dict[str, dict[str, Any]] = {}
    for label, path, params in (
        ("titles", "titles.json", {}),
        ("versions", f"versions/title-{TITLE}.json", {"part": str(PART)}),
    ):
        for _, encoding in ENCODING_VARIANTS:
            probe, response = await fetch(client, throttle, label, path, params, encoding)
            probes.append(probe)
            if encoding == "gzip" and probe.status == 200:
                samples[label] = response.json()

    report: dict[str, Any] = {"probes": [p.__dict__ for p in probes]}
    if "titles" in samples:
        entry = title_entry(samples["titles"], TITLE)
        report["title"] = entry
        report["titles_meta"] = samples["titles"].get("meta")
        write_sample(out_dir, "titles.json", samples["titles"])
        as_of = entry["up_to_date_as_of"]
        probe, response = await fetch(
            client,
            throttle,
            "full",
            f"full/{as_of}/title-{TITLE}.xml",
            {"part": str(PART)},
            "gzip",
        )
        probes.append(probe)
        report["probes"].append(probe.__dict__)
        report["full"] = {
            "date": as_of,
            "status": probe.status,
            "bytes": probe.body_bytes,
            "sha256": hashlib.sha256(response.content).hexdigest(),
            "sections": count_sections(response.text) if probe.status == 200 else None,
        }
    if "versions" in samples:
        report["versions"] = summarise_versions(samples["versions"])
        write_sample(out_dir, f"versions_title-{TITLE}_part-{PART}.json", samples["versions"])
    return report


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the eCFR versioner API (E11-T3 spike)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--rps",
        type=float,
        default=default_rps(),
        help="max requests per second (default: CONNECTOR_RATE_RPS_ECFR default)",
    )
    args = parser.parse_args(argv)
    async with make_client() as client:
        report = await run(client, Throttle(args.rps), args.out_dir)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    sys.exit(main())
