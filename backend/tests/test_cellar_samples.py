from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import spike_cellar_api as spike

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cellar"

# Counts recorded from the live endpoints on 2026-11-22 (docs/spikes/cellar-api.md). They are the
# recorded values, not the arch figures; the arch figures sit next to them so a drift is explicit.
RECORDED_VERSIONS = {"mifid2": 14, "mifir": 11, "priips": 5, "dr_2017_565": 6, "rts_2017_653": 7}
ARCH_VERSIONS = {"mifid2": 14, "mifir": 11, "priips": 5, "dr_2017_565": 6, "rts_2017_653": 7}
ARCH_TOTAL_PER_LANGUAGE = 43
RECORDED_LANGUAGES_RETURNED = {
    "mifid2": 23,
    "mifir": 24,
    "priips": 24,
    "dr_2017_565": 23,
    "rts_2017_653": 23,
}
RECORDED_CORRIGENDA = {"mifid2": 19, "mifir": 11, "priips": 8, "dr_2017_565": 9, "rts_2017_653": 8}
RECORDED_POLL_ROWS = 33
RECORDED_LATEST_MIFIR = "02014R0600-20251123"
# The only consolidated version without an English text.
RECORDED_NO_ENGLISH = "02017R0565-20170331"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw_celexes(slug: str) -> list[str]:
    # Counted straight from the recorded file, not through the spike helpers under test.
    bindings = load(f"sparql_versions_{slug}.json")["results"]["bindings"]
    return sorted({b["celex"]["value"] for b in bindings})


def summaries() -> list[dict[str, Any]]:
    result = []
    for act in spike.ACTS:
        rows = spike.parse_bindings(load(f"sparql_versions_{act.slug}.json"))
        languages = load(f"sparql_languages_{act.slug}.json")["languages_by_celex"]
        result.append(spike.summarise_act(act, rows, languages))
    return result


def test_cellar_samples_load() -> None:
    names = {p.name for p in FIXTURES.iterdir()}
    for act in spike.ACTS:
        assert {
            f"sparql_versions_{act.slug}.json",
            f"sparql_languages_{act.slug}.json",
            f"sparql_corrigenda_{act.slug}.json",
        } <= names
    assert {
        "sparql_poll.json",
        "sparql_corrigendum_languages.json",
        "rest_samples.json",
        "probes.json",
        "burst.json",
    } <= names

    total = 0
    for act in spike.ACTS:
        celexes = raw_celexes(act.slug)
        assert len(celexes) == RECORDED_VERSIONS[act.slug]
        assert all(c.startswith(act.consolidated_prefix) for c in celexes)
        languages = load(f"sparql_languages_{act.slug}.json")["languages_by_celex"]
        assert sorted(languages) == celexes
        for celex in celexes:
            # German and French are on every version; English on every one but a single version.
            assert {"DEU", "FRA"} <= set(languages[celex])
            assert ("ENG" in languages[celex]) == (celex != RECORDED_NO_ENGLISH)
        total += len(celexes)
        # Next to the arch figures: no difference for any act.
        assert len(celexes) == ARCH_VERSIONS[act.slug]
        corrigenda = load(f"sparql_corrigenda_{act.slug}.json")["results"]["bindings"]
        assert len(corrigenda) == RECORDED_CORRIGENDA[act.slug]
    assert total == ARCH_TOTAL_PER_LANGUAGE

    poll = load("sparql_poll.json")["results"]["bindings"]
    assert len(poll) == RECORDED_POLL_ROWS
    assert all(b["lm"]["datatype"].endswith("#dateTime") for b in poll)

    rest = load("rest_samples.json")
    assert rest["consolidated_celex"] == RECORDED_LATEST_MIFIR
    assert rest["corrigendum_celex"] == "32014R0600R(01)"
    assert rest["corrigendum_languages"] == ["FRA"]
    for key in ("xhtml_eng_head", "xhtml_deu_head", "xhtml_fra_head", "corrigendum_xhtml_head"):
        assert "<html" in rest[key]
    assert rest["formex_eng_members"] == [
        "CL2014R0600EN0100010.0001.doc.xml",
        "CL2014R0600EN0100010.0001.xml",
    ]


def test_recorded_behaviour_has_no_throttling() -> None:
    recorded = load("probes.json")
    statuses = {p["label"]: p["status"] for p in recorded["probes"]}
    assert 429 not in statuses.values()
    assert 503 not in statuses.values()
    assert statuses["text_html_eng"] == 404
    assert statuses["pdf_eng"] == 404
    assert statuses["xhtml_eng"] == 200
    assert statuses["formex_eng"] == 200
    # Two transient network failures (a read timeout and a read error) and nothing else non-200/404.
    assert {s for s in statuses.values() if s not in (200, 404)} == {0}
    assert sum(recorded["status_counts"].values()) == len(recorded["probes"])
    assert all(p["rate_headers"] == {} for p in recorded["probes"])
    burst = load("burst.json")
    assert burst["statuses"] == [200] * burst["size"] == [200] * 5
    assert burst["errors"] == []


def test_summaries_match_the_recorded_samples() -> None:
    by_slug = {s["slug"]: s for s in summaries()}
    for slug, found in RECORDED_VERSIONS.items():
        assert by_slug[slug]["found"] == found
        assert by_slug[slug]["difference"] == 0
        assert len(by_slug[slug]["languages_returned"]) == RECORDED_LANGUAGES_RETURNED[slug]
    assert by_slug["dr_2017_565"]["versions_with"] == {"ENG": 5, "DEU": 6, "FRA": 6}
    missing = [
        v["celex"] for v in by_slug["dr_2017_565"]["versions"] if v["missing_languages"] == ["ENG"]
    ]
    assert missing == [RECORDED_NO_ENGLISH]
    comparison = spike.compare_with_arch(list(by_slug.values()))
    assert comparison["arch_per_language"] == ARCH_TOTAL_PER_LANGUAGE
    assert comparison["found_versions"] == ARCH_TOTAL_PER_LANGUAGE
    assert comparison["difference"] == 0
    assert comparison["found_per_language"] == {"ENG": 42, "DEU": 43, "FRA": 43}
    assert comparison["per_act"]["mifir"] == {"found": 11, "arch": 11, "difference": 0}
    assert spike.latest_celex(by_slug["mifir"]) == RECORDED_LATEST_MIFIR


def test_last_modification_dates_are_recorded_for_every_version() -> None:
    for summary in summaries():
        assert all(v["last_modification"] for v in summary["versions"])


def test_summarise_act_keeps_the_latest_modification_date_and_flags_missing_languages() -> None:
    act = spike.ACTS[1]
    rows = [
        {"celex": "02014R0600-20240101", "w": "w1", "lm": "2026-01-01T00:00:00Z"},
        {"celex": "02014R0600-20240101", "w": "w1", "lm": "2026-03-01T00:00:00Z"},
        {"celex": "02014R0600-20240101", "w": "w1", "lm": "2026-02-01T00:00:00Z"},
        {"celex": "02014R0600-20230101", "w": "w0"},
    ]
    summary = spike.summarise_act(act, rows, {"02014R0600-20240101": ["DEU", "ENG"]})
    assert [v["celex"] for v in summary["versions"]] == [
        "02014R0600-20230101",
        "02014R0600-20240101",
    ]
    older, newer = summary["versions"]
    assert older["last_modification"] is None
    assert older["missing_languages"] == ["ENG", "DEU", "FRA"]
    assert newer["last_modification"] == "2026-03-01T00:00:00Z"
    assert newer["missing_languages"] == ["FRA"]
    assert summary["found"] == 2
    assert summary["difference"] == 2 - act.arch_versions


def test_latest_celex_of_an_empty_act_is_none() -> None:
    assert spike.latest_celex({"versions": []}) is None


def test_acts_cover_the_five_arch_acts() -> None:
    assert sum(a.arch_versions for a in spike.ACTS) == ARCH_TOTAL_PER_LANGUAGE
    mifir = next(a for a in spike.ACTS if a.slug == "mifir")
    assert mifir.celex == "32014R0600"
    assert mifir.consolidated_prefix == "02014R0600-"
    assert mifir.corrigendum_prefix == "32014R0600R("


def test_queries_use_the_documented_properties() -> None:
    act = spike.ACTS[0]
    assert '"02014L0065-"' in spike.versions_query(act)
    assert "cmr:lastModificationDate" in spike.versions_query(act)
    assert "cdm:expression_uses_language" in spike.languages_query(act)
    assert '"32014L0065R("' in spike.corrigenda_query(act)
    poll = spike.poll_query(spike.ACTS, "2026-01-01T00:00:00Z")
    assert '"2026-01-01T00:00:00Z"^^xsd:dateTime' in poll
    assert all(f'"{a.consolidated_prefix}"' in poll for a in spike.ACTS)
    assert "http://publications.europa.eu/ontology/cdm/cmr#" in spike.PREFIXES


def test_corrigendum_languages_query_accepts_only_cellar_work_iris() -> None:
    work = "http://publications.europa.eu/resource/cellar/fe1d3742-760c-11e4-b593-01aa75ed71a1"
    assert f"<{work}>" in spike.corrigendum_languages_query(work)
    for bad in ("http://example.org/x", work + ">", work + ' "x', work + "\n"):
        with pytest.raises(ValueError, match="not a CELLAR work IRI"):
            spike.corrigendum_languages_query(bad)


def test_pick_language_prefers_en_de_fr_then_anything() -> None:
    assert spike.pick_language(("FRA", "DEU")) == "DEU"
    assert spike.pick_language(("FRA",)) == "FRA"
    assert spike.pick_language(("POL",)) == "POL"
    assert spike.pick_language(()) is None


def test_language_code_and_group_languages() -> None:
    uri = "http://publications.europa.eu/resource/authority/language/DEU"
    assert spike.language_code(uri) == "DEU"
    assert spike.language_code(uri + "/") == "DEU"
    rows = [
        {"celex": "b", "lang": uri},
        {"celex": "a", "lang": uri.replace("DEU", "FRA")},
        {"celex": "a", "lang": uri.replace("DEU", "ENG")},
        {"celex": "a", "lang": uri.replace("DEU", "ENG")},
    ]
    assert spike.group_languages(rows) == {"a": ["ENG", "FRA"], "b": ["DEU"]}


def test_https_url_only_upgrades_http() -> None:
    assert spike.https_url("http://publications.europa.eu/x") == "https://publications.europa.eu/x"
    assert spike.https_url("https://publications.europa.eu/x") == "https://publications.europa.eu/x"


def test_zip_members_lists_a_zip_and_tolerates_garbage() -> None:
    assert spike.zip_members(_zip(["a.xml", "b.xml"])) == ["a.xml", "b.xml"]
    assert spike.zip_members(b"not a zip") == []


def test_rate_headers_picks_only_limit_headers() -> None:
    headers = httpx.Headers(
        {"X-RateLimit-Remaining": "5", "Retry-After": "2", "Cache-Control": "no-store"}
    )
    assert spike.rate_headers(headers) == {"x-ratelimit-remaining": "5", "retry-after": "2"}


def test_check_since_accepts_iso_and_rejects_the_rest() -> None:
    assert spike.check_since("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    with pytest.raises(argparse.ArgumentTypeError):
        spike.check_since('2026"; DROP')


def test_configured_rps_defaults_to_the_settings_field_default() -> None:
    assert spike.configured_rps({}) == 1.0


def test_configured_rps_reads_the_env_variable() -> None:
    assert spike.configured_rps({"CONNECTOR_RATE_RPS_EURLEX": "0.5"}) == 0.5


def test_configured_rps_defaults_to_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_RATE_RPS_EURLEX", "0.25")
    assert spike.configured_rps() == 0.25


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_configured_rps_rejects_what_settings_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="CONNECTOR_RATE_RPS_EURLEX"):
        spike.configured_rps({"CONNECTOR_RATE_RPS_EURLEX": value})


def test_resolve_rps() -> None:
    assert spike.resolve_rps(None, 1.0, False) == 1.0
    assert spike.resolve_rps(0.5, 1.0, False) == 0.5
    assert spike.resolve_rps(1.0, 1.0, False) == 1.0
    assert spike.resolve_rps(2.0, 1.0, True) == 2.0
    with pytest.raises(ValueError, match="--allow-above-limit"):
        spike.resolve_rps(2.0, 1.0, False)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_throttle_spaces_calls_at_least_one_interval() -> None:
    fake = FakeClock()
    throttle = spike.Throttle(1.0, clock=fake.clock, sleep=fake.sleep)
    await throttle.wait()
    assert fake.sleeps == []
    fake.now += 0.25
    await throttle.wait()
    assert fake.sleeps == [pytest.approx(0.75)]
    fake.now += 5.0
    await throttle.wait()
    assert fake.sleeps == [pytest.approx(0.75)]


async def test_throttle_honours_a_slower_rate() -> None:
    fake = FakeClock()
    throttle = spike.Throttle(0.5, clock=fake.clock, sleep=fake.sleep)
    await throttle.wait()
    await throttle.wait()
    assert fake.sleeps == [pytest.approx(2.0)]


@pytest.mark.parametrize("rps", [0, -1.0])
def test_throttle_rejects_non_positive_rate(rps: float) -> None:
    with pytest.raises(ValueError):
        spike.Throttle(rps)


def _zip(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<x/>")
    return buffer.getvalue()


def _sparql_result(name: str) -> httpx.Response:
    return httpx.Response(200, json=load(name))


def _languages_for(query: str) -> str:
    for act in spike.ACTS:
        if f'"{act.consolidated_prefix}"' in query:
            return f"sparql_languages_{act.slug}.json"
    raise AssertionError(query)


def _versions_for(query: str, marker: str) -> str:
    for act in spike.ACTS:
        if marker.format(act=act) in query:
            return f"sparql_versions_{act.slug}.json"
    raise AssertionError(query)


def _sparql_handler(request: httpx.Request) -> httpx.Response:
    query = request.url.params["query"]
    if "expression_belongs_to_work <" in query:
        return _sparql_result("sparql_corrigendum_languages.json")
    if "cdm:expression_uses_language" in query:
        langs = load(_languages_for(query))["languages_by_celex"]
        rows = [
            {
                "celex": {"type": "literal", "value": celex},
                "lang": {"type": "uri", "value": f"http://x/authority/language/{code}"},
            }
            for celex, codes in langs.items()
            for code in codes
        ]
        return httpx.Response(200, json={"results": {"bindings": rows}})
    if "xsd:dateTime" in query:
        return _sparql_result("sparql_poll.json")
    if "?celex ?w ?lm" in query:
        return _sparql_result(
            _versions_for(query, 'STRSTARTS(STR(?celex), "{act.consolidated_prefix}")')
        )
    if "?celex ?w WHERE" in query:
        for act in spike.ACTS:
            if f'"{act.corrigendum_prefix}"' in query:
                return _sparql_result(f"sparql_corrigenda_{act.slug}.json")
    if "LIMIT 1" in query:
        return httpx.Response(200, json={"results": {"bindings": []}})
    raise AssertionError(query)


def _rest_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    accept = request.headers["accept"]
    rest = load("rest_samples.json")
    if path.endswith("/DOC_1"):
        return httpx.Response(200, text=rest["xhtml_eng_head"])
    if path.endswith("/zip"):
        return httpx.Response(200, content=_zip(rest["formex_eng_members"]))
    if accept == spike.XHTML:
        return httpx.Response(303, headers={"Location": f"http://x.invalid{path}/DOC_1"})
    if accept == spike.FORMEX:
        return httpx.Response(303, headers={"Location": f"http://x.invalid{path}/zip"})
    return httpx.Response(404)


def _handler(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/sparql"):
            return _sparql_handler(request)
        return _rest_handler(request)

    return httpx.MockTransport(handler)


def _throttle(fake: FakeClock, rps: float = 1.0) -> spike.Throttle:
    return spike.Throttle(rps, clock=fake.clock, sleep=fake.sleep)


async def test_run_probes_everything_through_the_throttle(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    fake = FakeClock()
    async with httpx.AsyncClient(transport=_handler(requests)) as client:
        report = await spike.run(client, _throttle(fake), tmp_path)

    # 5 acts x 2 SPARQL, poll, 5 corrigenda, corrigendum languages, then REST with 303 hops.
    assert len(requests) == 31
    assert len(fake.sleeps) == 30
    assert all(r.headers["user-agent"] == spike.USER_AGENT for r in requests)
    assert report["comparison"]["found_versions"] == ARCH_TOTAL_PER_LANGUAGE
    assert report["poll_rows"] == RECORDED_POLL_ROWS
    assert report["corrigenda"] == RECORDED_CORRIGENDA
    # 25 logical probes: the 303 hops are not counted, only the final status of each probe.
    assert report["status_counts"] == {200: 23, 404: 2}
    rest = {p["label"]: p for p in report["rest"]}
    assert [h["status"] for h in rest["xhtml_deu"]["hops"]] == [303, 200]
    assert rest["text_html_eng"]["status"] == 404
    assert rest["pdf_eng"]["status"] == 404
    assert rest["corrigendum_xhtml"]["status"] == 200
    # The 303 Location is http://; the next hop is upgraded to https.
    followed = [r for r in requests if r.url.path.endswith("/DOC_1")]
    assert followed
    assert all(r.url.scheme == "https" for r in followed)
    assert {r.headers["accept-language"] for r in followed} == {"eng", "deu", "fra"}
    corrigendum_urls = {r.url.path for r in requests if r.url.path.startswith("/resource/cellar/")}
    assert any(p.endswith("fe1d3742-760c-11e4-b593-01aa75ed71a1") for p in corrigendum_urls)
    written = {p.name for p in tmp_path.iterdir()}
    assert "sparql_versions_mifir.json" in written
    assert "sparql_corrigendum_languages.json" in written
    assert "probes.json" in written
    assert "burst.json" not in written
    samples = json.loads((tmp_path / "rest_samples.json").read_text())
    assert samples["consolidated_celex"] == RECORDED_LATEST_MIFIR
    assert samples["corrigendum_languages"] == ["FRA"]
    assert samples["formex_eng_members"] == load("rest_samples.json")["formex_eng_members"]
    assert samples["corrigendum_formex_members"] == samples["formex_eng_members"]


async def test_run_with_a_burst_records_it(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    fake = FakeClock()
    async with httpx.AsyncClient(transport=_handler(requests)) as client:
        report = await spike.run(client, _throttle(fake), tmp_path, burst=3)
    assert report["burst"]["statuses"] == [200, 200, 200]
    assert len(requests) == 31 + 3
    assert len(fake.sleeps) == 30
    assert json.loads((tmp_path / "burst.json").read_text())["size"] == 3


async def test_run_burst_records_statuses_and_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        burst = await spike.run_burst(client, 2)
    assert burst["statuses"] == [429, 429]
    assert burst["errors"] == []
    assert len(burst["request_seconds"]) == 2

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
        burst = await spike.run_burst(client, 2)
    assert burst["statuses"] == [0, 0]
    assert burst["errors"] == ["ConnectError", "ConnectError"]


async def test_run_records_unreachable_endpoints_and_does_not_fake_samples(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    fake = FakeClock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await spike.run(client, _throttle(fake), tmp_path)

    assert report["acts"] == []
    assert report["comparison"]["found_versions"] == 0
    assert report["poll_rows"] is None
    assert report["corrigenda"] == {}
    assert report["rest"] == []
    assert {p["status"] for p in report["probes"]} == {0}
    assert {p["error"] for p in report["probes"]} == {"ConnectError"}
    # 5 acts x 2 + poll + 5 corrigenda; without a SPARQL result there is no REST probe and no
    # corrigendum language lookup.
    assert len(report["probes"]) == 16
    assert sorted(p.name for p in tmp_path.iterdir()) == ["probes.json", "rest_samples.json"]


async def test_sparql_records_429_with_its_rate_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2", "Content-Type": "text/plain"})

    fake = FakeClock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe, payload = await spike.sparql(client, _throttle(fake), "x", "SELECT 1")
    assert payload is None
    assert probe.status == 429
    assert probe.rate_headers == {"retry-after": "2"}
    assert probe.error is None


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(200, text="not json"), "unreadable SPARQL result: JSONDecodeError"),
        (httpx.Response(200, json={"nope": 1}), "unreadable SPARQL result: KeyError"),
    ],
)
async def test_sparql_records_an_unreadable_answer(response: httpx.Response, error: str) -> None:
    fake = FakeClock()
    transport = httpx.MockTransport(lambda request: response)
    async with httpx.AsyncClient(transport=transport) as client:
        probe, payload = await spike.sparql(client, _throttle(fake), "x", "SELECT 1")
    assert payload is None
    assert probe.status == 200
    assert probe.error == error


async def test_fetch_stops_on_a_redirect_without_location() -> None:
    fake = FakeClock()
    transport = httpx.MockTransport(lambda request: httpx.Response(303))
    async with httpx.AsyncClient(transport=transport) as client:
        probe, _ = await spike.fetch(client, _throttle(fake), "x", "https://h.invalid/a", "a/b")
    assert probe.status == 303
    assert len(probe.hops) == 1


async def test_fetch_bounds_a_redirect_loop() -> None:
    fake = FakeClock()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(303, headers={"Location": "/again"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        probe, _ = await spike.fetch(client, _throttle(fake), "x", "https://h.invalid/a", "a/b")
    assert probe.status == 303
    assert len(probe.hops) == spike.MAX_REDIRECTS + 1
    assert len(fake.sleeps) == spike.MAX_REDIRECTS


async def test_fetch_sets_language_header_and_records_content_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, content=b"abc", headers={"Content-Type": "text/x", "Content-Language": "en"}
        )

    fake = FakeClock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe, _ = await spike.fetch(
            client, _throttle(fake), "x", "https://h.invalid/a", "a/b", "DEU"
        )
    assert seen[0].headers["accept-language"] == "deu"
    assert (probe.content_type, probe.content_language, probe.body_bytes) == ("text/x", "en", 3)


async def test_find_corrigendum_without_rows_or_without_an_answer(tmp_path: Path) -> None:
    fake = FakeClock()
    async with httpx.AsyncClient(transport=_handler([])) as client:
        assert await spike.find_corrigendum(client, _throttle(fake), tmp_path, []) == (None, [])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    rows = [{"celex": "32014R0600R(01)", "w": spike.CELLAR_WORK_PREFIX + "abc"}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found, probes = await spike.find_corrigendum(client, _throttle(fake), tmp_path, rows)
    assert found is None
    assert [p.status for p in probes] == [500]
    assert list(tmp_path.iterdir()) == []


def test_rest_requests_need_what_they_ask_for() -> None:
    corrigendum = spike.Corrigendum(
        "c", "http://publications.europa.eu/resource/cellar/w", ("FRA",)
    )
    assert spike.rest_requests(None, None) == []
    only_consolidated = spike.rest_requests("0X", None)
    assert [r[0] for r in only_consolidated] == [v.label for v in spike.REST_VARIANTS]
    assert all(r[1] == f"{spike.REST_BASE}/0X" for r in only_consolidated)
    only_corrigendum = spike.rest_requests(None, corrigendum)
    assert only_corrigendum == [
        (
            "corrigendum_xhtml",
            "https://publications.europa.eu/resource/cellar/w",
            spike.XHTML,
            "FRA",
        ),
        (
            "corrigendum_formex",
            "https://publications.europa.eu/resource/cellar/w",
            spike.FORMEX,
            "FRA",
        ),
    ]
    no_language = spike.Corrigendum("c", "http://publications.europa.eu/resource/cellar/w", ())
    assert spike.rest_requests(None, no_language) == []


async def test_main_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeClock()
    monkeypatch.setattr(spike, "make_client", lambda: httpx.AsyncClient(transport=_handler([])))
    real_throttle = spike.Throttle
    monkeypatch.setattr(
        spike, "Throttle", lambda rps: real_throttle(rps, clock=fake.clock, sleep=fake.sleep)
    )
    monkeypatch.delenv("CONNECTOR_RATE_RPS_EURLEX", raising=False)
    argv = ["--out-dir", str(tmp_path), "--rps", "2", "--allow-above-limit", "--burst", "2"]
    assert await spike.amain(argv) == 0
    out = capsys.readouterr().out
    # Other tests may route structlog to stdout; the report is the JSON object that starts a line.
    report = json.loads(out[out.index("{\n") :])
    assert report["comparison"]["found_versions"] == ARCH_TOTAL_PER_LANGUAGE
    assert report["burst"]["statuses"] == [200, 200]
    assert fake.sleeps == [pytest.approx(0.5)] * 30


async def test_main_default_rate_is_the_configured_knob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeClock()
    monkeypatch.setattr(spike, "make_client", lambda: httpx.AsyncClient(transport=_handler([])))
    real_throttle = spike.Throttle
    monkeypatch.setattr(
        spike, "Throttle", lambda rps: real_throttle(rps, clock=fake.clock, sleep=fake.sleep)
    )
    monkeypatch.setenv("CONNECTOR_RATE_RPS_EURLEX", "4")
    assert await spike.amain(["--out-dir", str(tmp_path)]) == 0
    assert fake.sleeps == [pytest.approx(0.25)] * 30


@pytest.mark.parametrize(
    "argv",
    [["--rps", "5"], ["--burst", "3"], ["--burst", "-1", "--allow-above-limit"], ["--since", "x"]],
)
async def test_main_refuses_what_needs_the_override(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        spike, "make_client", lambda: httpx.AsyncClient(transport=_handler(requests))
    )
    monkeypatch.delenv("CONNECTOR_RATE_RPS_EURLEX", raising=False)
    with pytest.raises(SystemExit) as exc:
        await spike.amain(["--out-dir", str(tmp_path), *argv])
    assert exc.value.code == 2
    assert requests == []


def test_documented_command_runs_from_backend() -> None:
    # The documented way (docs/spikes/cellar-api.md): `python -m scripts.spike_cellar_api`.
    result = subprocess.run(
        [sys.executable, "-m", "scripts.spike_cellar_api", "--help"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--allow-above-limit" in result.stdout
    assert "--burst" in result.stdout


def test_main_runs_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_amain(argv: list[str] | None) -> int:
        return 0

    monkeypatch.setattr(spike, "amain", fake_amain)
    assert spike.main([]) == 0
