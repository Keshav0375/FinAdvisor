"""E11-T2 spike: probe EUR-Lex CELLAR (SPARQL and REST) and record samples.

Run by hand, never in the PR gate:

    cd backend && python -m scripts.spike_cellar_api --out-dir tests/fixtures/cellar

It must run as a module from backend/ (not `python scripts/spike_cellar_api.py`), so `src` imports.
Requests are spaced at least 1/CONNECTOR_RATE_RPS_EURLEX seconds apart (a higher --rps is refused
unless --allow-above-limit is given), with a descriptive User-Agent and no personal data. A burst
is above that rate by definition, so --burst N also needs --allow-above-limit. The findings are
written up in docs/spikes/cellar-api.md.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, NamedTuple
from urllib.parse import urljoin

import httpx
import structlog
from pydantic import TypeAdapter, ValidationError

from src.config import Settings

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "cellar"
# The URLs, USER_AGENT, TIMEOUT_S and the other constants below are spike-local on purpose: this
# script is run by hand, once. The connector (E11-T18) moves the URLs and timeout into Settings as
# EURLEX knobs, together with the throttle, so the single typed config stays the only place for
# tunables.
SPARQL_URL = "https://publications.europa.eu/webapi/rdf/sparql"
REST_BASE = "https://publications.europa.eu/resource/celex"
USER_AGENT = "FinAdvisor-spike/0.1 (CELLAR API probe; research prototype)"
TIMEOUT_S = 60.0
MAX_REDIRECTS = 5
RPS_ENV = "CONNECTOR_RATE_RPS_EURLEX"
# Only changes after this instant count as modified in the poll query probe.
POLL_SINCE = "2026-01-01T00:00:00Z"
# Authority-table language codes (publications.europa.eu/resource/authority/language/<code>).
LANGUAGES = ("ENG", "DEU", "FRA")
XHTML = "application/xhtml+xml"
FORMEX = "application/zip;mtype=fmx4"
# Response headers that could carry a vendor limit; recorded when present.
RATE_HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "retry-after")
SNIPPET_CHARS = 1500
PREFIXES = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
    "PREFIX cmr: <http://publications.europa.eu/ontology/cdm/cmr#>\n"
    "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
)
BURST_QUERY = (
    PREFIXES + 'SELECT ?w WHERE { ?w cdm:resource_legal_id_celex "32014R0600"^^xsd:string } LIMIT 1'
)


@dataclass(frozen=True)
class Act:
    slug: str
    name: str
    celex: str  # natural CELEX of the base act
    arch_versions: int  # consolidated versions per language, deep-dive/11-tech-stack.md

    @property
    def consolidated_prefix(self) -> str:
        return f"0{self.celex[1:]}-"

    @property
    def corrigendum_prefix(self) -> str:
        return f"{self.celex}R("


ACTS: tuple[Act, ...] = (
    Act("mifid2", "MiFID II (Directive 2014/65/EU)", "32014L0065", 14),
    Act("mifir", "MiFIR (Regulation (EU) No 600/2014)", "32014R0600", 11),
    Act("priips", "PRIIPs (Regulation (EU) No 1286/2014)", "32014R1286", 5),
    Act("dr_2017_565", "Delegated Regulation (EU) 2017/565", "32017R0565", 6),
    Act("rts_2017_653", "RTS, Delegated Regulation (EU) 2017/653", "32017R0653", 7),
)


class RestVariant(NamedTuple):
    label: str
    accept: str
    language: str


# Content negotiation on the latest consolidated MiFIR version.
REST_VARIANTS: tuple[RestVariant, ...] = (
    RestVariant("xhtml_eng", XHTML, "ENG"),
    RestVariant("xhtml_deu", XHTML, "DEU"),
    RestVariant("xhtml_fra", XHTML, "FRA"),
    RestVariant("formex_eng", FORMEX, "ENG"),
    RestVariant("text_html_eng", "text/html", "ENG"),
    RestVariant("pdf_eng", "application/pdf", "ENG"),
)
# Content types asked of a corrigendum, in the language it exists in.
CORRIGENDUM_ACCEPTS: tuple[tuple[str, str], ...] = (
    ("corrigendum_xhtml", XHTML),
    ("corrigendum_formex", FORMEX),
)
CELLAR_WORK_PREFIX = "http://publications.europa.eu/resource/cellar/"


@dataclass(frozen=True)
class Corrigendum:
    celex: str
    work: str  # CELLAR work IRI: a corrigendum CELEX is not resolvable through the REST celex alias
    languages: tuple[str, ...]


@dataclass(frozen=True)
class Hop:
    url: str
    status: int  # 0 when no HTTP response arrived
    location: str | None
    seconds: float
    error: str | None = None


@dataclass
class Probe:
    label: str
    accept: str
    language: str | None
    hops: list[Hop] = field(default_factory=list)
    status: int = 0
    content_type: str | None = None
    content_language: str | None = None
    body_bytes: int = 0
    rate_headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


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


def configured_rps(environ: Mapping[str, str] | None = None) -> float:
    """Settings.eurlex_rate_limit_rps: CONNECTOR_RATE_RPS_EURLEX if set, else the field default.

    Validated with the Settings field's own constraints, without building Settings (which would
    need every secret for a script that only probes a public API).
    """
    env = os.environ if environ is None else environ
    field_info = Settings.model_fields["eurlex_rate_limit_rps"]
    raw = env.get(RPS_ENV)
    if raw is None:
        return float(field_info.default)
    adapter: TypeAdapter[float] = TypeAdapter(Annotated[float, *field_info.metadata])
    try:
        return float(adapter.validate_python(raw))
    except ValidationError as exc:
        raise ValueError(f"{RPS_ENV}={raw!r} is invalid: {exc.errors()[0]['msg']}") from exc


def resolve_rps(requested: float | None, configured: float, allow_above_limit: bool) -> float:
    """The rate to use: the configured one, or a lower request; higher only when forced."""
    if requested is None:
        return configured
    if requested > configured and not allow_above_limit:
        raise ValueError(
            f"--rps {requested} is above the configured limit {configured} ({RPS_ENV}); "
            "pass --allow-above-limit to override"
        )
    return requested


def check_since(value: str) -> str:
    """The poll cut-off goes into the query text, so it must be an ISO 8601 instant."""
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO 8601 date-time") from exc
    return value


def versions_query(act: Act) -> str:
    return (
        PREFIXES
        + "SELECT ?celex ?w ?lm WHERE { ?w cdm:resource_legal_id_celex ?celex . "
        + "OPTIONAL { ?w cmr:lastModificationDate ?lm } "
        + f'FILTER(STRSTARTS(STR(?celex), "{act.consolidated_prefix}")) }} ORDER BY ?celex'
    )


def languages_query(act: Act) -> str:
    return (
        PREFIXES
        + "SELECT ?celex ?lang WHERE { ?w cdm:resource_legal_id_celex ?celex . "
        + "?e cdm:expression_belongs_to_work ?w . ?e cdm:expression_uses_language ?lang . "
        + f'FILTER(STRSTARTS(STR(?celex), "{act.consolidated_prefix}")) }} ORDER BY ?celex ?lang'
    )


def poll_query(acts: tuple[Act, ...], since: str) -> str:
    starts = " || ".join(f'STRSTARTS(STR(?celex), "{a.consolidated_prefix}")' for a in acts)
    return (
        PREFIXES
        + "SELECT ?celex ?lm WHERE { ?w cdm:resource_legal_id_celex ?celex . "
        + "?w cmr:lastModificationDate ?lm . "
        + f'FILTER({starts}) FILTER(?lm >= "{since}"^^xsd:dateTime) }} ORDER BY ?lm'
    )


def corrigenda_query(act: Act) -> str:
    return (
        PREFIXES
        + "SELECT ?celex ?w WHERE { ?w cdm:resource_legal_id_celex ?celex . "
        + f'FILTER(STRSTARTS(STR(?celex), "{act.corrigendum_prefix}")) }} ORDER BY ?celex'
    )


def corrigendum_languages_query(work: str) -> str:
    # The IRI comes from an earlier SPARQL answer; refuse anything that is not a CELLAR work IRI.
    if not work.startswith(CELLAR_WORK_PREFIX) or any(c in work for c in '<>"\\ \n'):
        raise ValueError(f"not a CELLAR work IRI: {work!r}")
    return (
        PREFIXES
        + f"SELECT ?lang WHERE {{ ?e cdm:expression_belongs_to_work <{work}> . "
        + "?e cdm:expression_uses_language ?lang }"
    )


def pick_language(languages: tuple[str, ...]) -> str | None:
    """The first of EN, DE, FR the corrigendum exists in, else any language, else none."""
    for wanted in LANGUAGES:
        if wanted in languages:
            return wanted
    return languages[0] if languages else None


def parse_bindings(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {name: cell["value"] for name, cell in binding.items()}
        for binding in payload["results"]["bindings"]
    ]


def language_code(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]


def group_languages(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = {}
    for row in rows:
        grouped.setdefault(row["celex"], set()).add(language_code(row["lang"]))
    return {celex: sorted(codes) for celex, codes in sorted(grouped.items())}


def summarise_act(
    act: Act,
    version_rows: list[dict[str, str]],
    languages_by_celex: Mapping[str, list[str]],
) -> dict[str, Any]:
    by_celex: dict[str, dict[str, Any]] = {}
    for row in version_rows:
        entry = by_celex.setdefault(
            row["celex"], {"celex": row["celex"], "work": row["w"], "last_modification": None}
        )
        modified = row.get("lm")
        # The query can return more than one modification date for a work: keep the latest.
        if modified is not None and (
            entry["last_modification"] is None or modified > entry["last_modification"]
        ):
            entry["last_modification"] = modified
    versions = []
    for celex in sorted(by_celex):
        languages = list(languages_by_celex.get(celex, []))
        versions.append(
            {
                **by_celex[celex],
                "languages": languages,
                "missing_languages": [lang for lang in LANGUAGES if lang not in languages],
            }
        )
    return {
        "slug": act.slug,
        "name": act.name,
        "celex": act.celex,
        "arch_versions": act.arch_versions,
        "found": len(versions),
        "difference": len(versions) - act.arch_versions,
        "languages_returned": sorted({lang for v in versions for lang in v["languages"]}),
        "versions_with": {
            lang: sum(1 for v in versions if lang in v["languages"]) for lang in LANGUAGES
        },
        "versions": versions,
    }


def compare_with_arch(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    arch_total = sum(s["arch_versions"] for s in summaries)
    found_total = sum(s["found"] for s in summaries)
    return {
        "arch_per_language": arch_total,
        "found_versions": found_total,
        "difference": found_total - arch_total,
        "found_per_language": {
            lang: sum(s["versions_with"][lang] for s in summaries) for lang in LANGUAGES
        },
        "per_act": {
            s["slug"]: {
                "found": s["found"],
                "arch": s["arch_versions"],
                "difference": s["difference"],
            }
            for s in summaries
        },
    }


def latest_celex(summary: dict[str, Any]) -> str | None:
    versions = summary["versions"]
    return str(versions[-1]["celex"]) if versions else None


def rate_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower().startswith(RATE_HEADER_PREFIXES)}


def https_url(url: str) -> str:
    """CELLAR answers its 303 with an http:// Location; the next hop goes to https."""
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


def zip_members(content: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return archive.namelist()
    except zipfile.BadZipFile:
        return []


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


async def get_once(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict[str, str] | None,
) -> tuple[Hop, httpx.Response | None]:
    started = time.monotonic()
    try:
        response = await client.get(
            url, headers=headers, params=params, timeout=TIMEOUT_S, follow_redirects=False
        )
    except httpx.HTTPError as exc:
        # An unreachable endpoint is a finding, not a crash.
        hop = Hop(url, 0, None, time.monotonic() - started, error=type(exc).__name__)
        return hop, None
    hop = Hop(
        url,
        response.status_code,
        response.headers.get("location"),
        time.monotonic() - started,
    )
    return hop, response


async def fetch(
    client: httpx.AsyncClient,
    throttle: Throttle,
    label: str,
    url: str,
    accept: str,
    language: str | None = None,
    params: dict[str, str] | None = None,
) -> tuple[Probe, httpx.Response | None]:
    """One logical request. Every hop of a 303 chain goes through the throttle."""
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if language is not None:
        headers["Accept-Language"] = language.lower()
    probe = Probe(label=label, accept=accept, language=language)
    response: httpx.Response | None = None
    next_url, next_params = url, params
    for _ in range(MAX_REDIRECTS + 1):
        await throttle.wait()
        hop, response = await get_once(client, next_url, headers, next_params)
        probe.hops.append(hop)
        if response is None or not 300 <= response.status_code < 400 or not hop.location:
            break
        next_url, next_params = https_url(urljoin(next_url, hop.location)), None
    last = probe.hops[-1]
    probe.status = last.status
    probe.error = last.error
    if response is not None:
        probe.content_type = response.headers.get("content-type")
        probe.content_language = response.headers.get("content-language")
        probe.body_bytes = len(response.content)
        probe.rate_headers = rate_headers(response.headers)
    log.info("probe", label=label, status=probe.status, hops=len(probe.hops), error=probe.error)
    return probe, response


async def sparql(
    client: httpx.AsyncClient, throttle: Throttle, label: str, query: str
) -> tuple[Probe, dict[str, Any] | None]:
    probe, response = await fetch(
        client,
        throttle,
        label,
        SPARQL_URL,
        "application/sparql-results+json",
        params={"query": query},
    )
    if response is None or probe.status != 200:
        return probe, None
    try:
        payload: dict[str, Any] = response.json()
        parse_bindings(payload)
    except (ValueError, KeyError, TypeError) as exc:
        probe.error = f"unreadable SPARQL result: {type(exc).__name__}"
        return probe, None
    return probe, payload


async def run_burst(client: httpx.AsyncClient, size: int) -> dict[str, Any]:
    """`size` tiny SPARQL queries sent at once, without the throttle."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}

    async def one() -> Hop:
        hop, _ = await get_once(client, SPARQL_URL, headers, {"query": BURST_QUERY})
        return hop

    started = time.monotonic()
    hops = await asyncio.gather(*(one() for _ in range(size)))
    return {
        "size": size,
        "statuses": [h.status for h in hops],
        "errors": [h.error for h in hops if h.error],
        "request_seconds": [round(h.seconds, 3) for h in hops],
        "total_seconds": round(time.monotonic() - started, 3),
    }


def write_sample(out_dir: Path, name: str, payload: Any) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    target.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def rest_requests(
    consolidated: str | None, corrigendum: Corrigendum | None
) -> list[tuple[str, str, str, str]]:
    """(label, url, accept, language) for every REST probe that has what it needs."""
    requests: list[tuple[str, str, str, str]] = []
    if consolidated is not None:
        requests.extend(
            (v.label, f"{REST_BASE}/{consolidated}", v.accept, v.language) for v in REST_VARIANTS
        )
    language = pick_language(corrigendum.languages) if corrigendum else None
    if corrigendum is not None and language is not None:
        work_url = https_url(corrigendum.work)
        requests.extend(
            (label, work_url, accept, language) for label, accept in CORRIGENDUM_ACCEPTS
        )
    return requests


async def probe_rest(
    client: httpx.AsyncClient,
    throttle: Throttle,
    consolidated: str | None,
    corrigendum: Corrigendum | None,
) -> tuple[list[Probe], dict[str, Any]]:
    probes: list[Probe] = []
    samples: dict[str, Any] = {
        "consolidated_celex": consolidated,
        "corrigendum_celex": corrigendum.celex if corrigendum else None,
        "corrigendum_languages": list(corrigendum.languages) if corrigendum else None,
    }
    for label, url, accept, language in rest_requests(consolidated, corrigendum):
        probe, response = await fetch(client, throttle, label, url, accept, language)
        probes.append(probe)
        if response is None or probe.status != 200:
            continue
        if accept == FORMEX:
            samples[f"{label}_members"] = zip_members(response.content)
        elif accept == XHTML:
            samples[f"{label}_head"] = response.text[:SNIPPET_CHARS]
    return probes, samples


async def find_corrigendum(
    client: httpx.AsyncClient,
    throttle: Throttle,
    out_dir: Path,
    rows: list[dict[str, str]],
) -> tuple[Corrigendum | None, list[Probe]]:
    """The first corrigendum of an act and the languages it exists in (usually not all)."""
    if not rows:
        return None, []
    first = rows[0]
    probe, payload = await sparql(
        client, throttle, "corrigendum_languages", corrigendum_languages_query(first["w"])
    )
    if payload is None:
        return None, [probe]
    write_sample(out_dir, "sparql_corrigendum_languages.json", payload)
    codes = sorted({language_code(row["lang"]) for row in parse_bindings(payload)})
    return Corrigendum(first["celex"], first["w"], tuple(codes)), [probe]


async def run(
    client: httpx.AsyncClient,
    throttle: Throttle,
    out_dir: Path,
    since: str = POLL_SINCE,
    burst: int = 0,
) -> dict[str, Any]:
    probes: list[Probe] = []
    summaries: list[dict[str, Any]] = []
    corrigenda: dict[str, list[dict[str, str]]] = {}
    for act in ACTS:
        probe, versions = await sparql(
            client, throttle, f"versions_{act.slug}", versions_query(act)
        )
        probes.append(probe)
        probe, languages = await sparql(
            client, throttle, f"languages_{act.slug}", languages_query(act)
        )
        probes.append(probe)
        if versions is None or languages is None:
            continue
        by_celex = group_languages(parse_bindings(languages))
        summaries.append(summarise_act(act, parse_bindings(versions), by_celex))
        write_sample(out_dir, f"sparql_versions_{act.slug}.json", versions)
        write_sample(out_dir, f"sparql_languages_{act.slug}.json", {"languages_by_celex": by_celex})

    probe, poll = await sparql(client, throttle, "poll_last_modification", poll_query(ACTS, since))
    probes.append(probe)
    if poll is not None:
        write_sample(out_dir, "sparql_poll.json", poll)

    for act in ACTS:
        probe, found = await sparql(
            client, throttle, f"corrigenda_{act.slug}", corrigenda_query(act)
        )
        probes.append(probe)
        if found is not None:
            corrigenda[act.slug] = parse_bindings(found)
            write_sample(out_dir, f"sparql_corrigenda_{act.slug}.json", found)

    mifir = next((s for s in summaries if s["slug"] == "mifir"), None)
    consolidated = latest_celex(mifir) if mifir else None
    corrigendum, language_probes = await find_corrigendum(
        client, throttle, out_dir, corrigenda.get("mifir", [])
    )
    probes.extend(language_probes)
    rest_probes, rest_samples = await probe_rest(client, throttle, consolidated, corrigendum)
    probes.extend(rest_probes)
    write_sample(out_dir, "rest_samples.json", rest_samples)

    report: dict[str, Any] = {
        "since": since,
        "acts": summaries,
        "comparison": compare_with_arch(summaries),
        "poll_rows": len(parse_bindings(poll)) if poll is not None else None,
        "corrigenda": {slug: len(rows) for slug, rows in corrigenda.items()},
        "rest": [asdict(p) for p in rest_probes],
        "status_counts": dict(Counter(p.status for p in probes)),
        "probes": [asdict(p) for p in probes],
    }
    if burst:
        report["burst"] = await run_burst(client, burst)
    write_sample(out_dir, "probes.json", {k: report[k] for k in ("status_counts", "probes")})
    if "burst" in report:
        write_sample(out_dir, "burst.json", report["burst"])
    return report


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe EUR-Lex CELLAR (E11-T2 spike)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--rps",
        type=float,
        default=None,
        help=f"requests per second (default and maximum: {RPS_ENV}, default 1.0)",
    )
    parser.add_argument(
        "--allow-above-limit",
        action="store_true",
        help="allow --rps and --burst above the configured limit",
    )
    parser.add_argument(
        "--burst",
        type=int,
        default=0,
        metavar="N",
        help="also send N tiny SPARQL queries at once (needs --allow-above-limit)",
    )
    parser.add_argument("--since", type=check_since, default=POLL_SINCE)
    args = parser.parse_args(argv)
    if args.burst < 0:
        parser.error("--burst must not be negative")
    if args.burst and not args.allow_above_limit:
        parser.error("--burst is above the configured limit; pass --allow-above-limit to override")
    try:
        rps = resolve_rps(args.rps, configured_rps(), args.allow_above_limit)
    except ValueError as exc:
        parser.error(str(exc))
    async with make_client() as client:
        report = await run(client, Throttle(rps), args.out_dir, args.since, args.burst)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    sys.exit(main())
