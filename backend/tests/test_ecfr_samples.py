from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import spike_ecfr_api as spike
from src.config import Settings

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ecfr"

# Counts recorded from the live API on 2026-11-21 (docs/spikes/ecfr-api.md). The arch
# (11-tech-stack.md, source connectors) says 143 versions and 9 removed; the live API returned 145.
RECORDED_VERSIONS = 145
RECORDED_REMOVED = 9
RECORDED_NON_SUBSTANTIVE = 28
ARCH_VERSIONS = 143
ARCH_REMOVED = 9
RECORDED_UP_TO_DATE_AS_OF = "2026-11-19"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def test_ecfr_samples_load() -> None:
    versions = load("versions_title-17_part-275.json")
    titles = load("titles.json")

    assert "content_versions" in versions
    assert "titles" in titles
    summary = spike.summarise_versions(versions)
    assert summary["records"] == RECORDED_VERSIONS
    assert summary["removed"] == RECORDED_REMOVED
    assert summary["non_substantive"] == RECORDED_NON_SUBSTANTIVE
    assert RECORDED_VERSIONS - ARCH_VERSIONS == 2
    assert RECORDED_REMOVED == ARCH_REMOVED
    assert titles["meta"]["date"] == RECORDED_UP_TO_DATE_AS_OF
    assert spike.title_entry(titles, 17)["up_to_date_as_of"] == RECORDED_UP_TO_DATE_AS_OF


def test_version_key_is_unique_per_record() -> None:
    # The arch diffs on (identifier, amendment_date, issue_date); it must identify a record.
    records = load("versions_title-17_part-275.json")["content_versions"]
    keys = {(r["identifier"], r["amendment_date"], r["issue_date"]) for r in records}
    assert len(keys) == len(records)


def test_summarise_versions_fields_and_dates() -> None:
    summary = spike.summarise_versions(load("versions_title-17_part-275.json"))
    assert summary["first_amendment_date"] == "2016-12-31"
    assert summary["last_amendment_date"] == "2026-11-06"
    assert {"removed", "substantive", "identifier", "amendment_date", "issue_date"} <= set(
        summary["fields"]
    )


def test_summarise_versions_empty() -> None:
    summary = spike.summarise_versions({"content_versions": []})
    assert summary["records"] == 0
    assert summary["first_amendment_date"] is None


def test_title_entry_missing_raises() -> None:
    with pytest.raises(KeyError):
        spike.title_entry({"titles": [{"number": 1}]}, 17)


def test_count_sections() -> None:
    xml = '<DIV8 TYPE="SECTION"/><DIV8 TYPE="SECTION"/><DIV5 TYPE="PART"/>'
    assert spike.count_sections(xml) == 2


def test_build_headers() -> None:
    assert spike.build_headers("gzip")["Accept-Encoding"] == "gzip"
    omitted = spike.build_headers(None)
    assert "Accept-Encoding" not in omitted
    assert omitted["User-Agent"] == spike.USER_AGENT


def test_user_agent_has_no_personal_data() -> None:
    assert "@" not in spike.USER_AGENT
    assert "FinAdvisor" in spike.USER_AGENT


def test_rate_headers_picks_only_limit_headers() -> None:
    headers = httpx.Headers(
        {"X-RateLimit-Remaining": "5", "Retry-After": "2", "Cache-Control": "no-store"}
    )
    assert spike.rate_headers(headers) == {"x-ratelimit-remaining": "5", "retry-after": "2"}


def test_default_rps_is_the_settings_default() -> None:
    assert spike.default_rps() == 1.0
    assert Settings.model_fields["ecfr_rate_limit_rps"].default == 1.0


async def test_make_client_sends_no_default_accept_encoding() -> None:
    async with spike.make_client() as client:
        assert "accept-encoding" not in client.headers


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


def _transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    versions = load("versions_title-17_part-275.json")
    titles = load("titles.json")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/titles.json"):
            return httpx.Response(200, json=titles)
        if "/versions/" in path:
            return httpx.Response(200, json=versions)
        if "/full/" in path:
            return httpx.Response(200, text='<DIV8 TYPE="SECTION"/>')
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_run_records_probes_samples_and_report(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    fake = FakeClock()
    throttle = spike.Throttle(1.0, clock=fake.clock, sleep=fake.sleep)
    async with httpx.AsyncClient(transport=_transport(requests)) as client:
        client.headers.pop("Accept-Encoding", None)
        report = await spike.run(client, throttle, tmp_path)

    assert len(requests) == 7
    assert [r.headers.get("accept-encoding") for r in requests[:3]] == ["gzip", "identity", None]
    assert all(r.headers["user-agent"] == spike.USER_AGENT for r in requests)
    assert requests[-1].url.path.endswith("/full/2026-11-19/title-17.xml")
    assert requests[-1].url.params["part"] == "275"
    assert report["versions"]["records"] == RECORDED_VERSIONS
    assert report["full"]["sections"] == 1
    assert [p["status"] for p in report["probes"]] == [200] * 7
    assert len(fake.sleeps) == 6
    assert json.loads((tmp_path / "titles.json").read_text())["meta"]["date"] == "2026-11-19"
    written = json.loads((tmp_path / "versions_title-17_part-275.json").read_text())
    assert len(written["content_versions"]) == RECORDED_VERSIONS


async def test_run_records_406_and_writes_no_sample(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(406)

    fake = FakeClock()
    throttle = spike.Throttle(1.0, clock=fake.clock, sleep=fake.sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await spike.run(client, throttle, tmp_path)

    assert [p["status"] for p in report["probes"]] == [406] * 6
    assert "full" not in report
    assert list(tmp_path.iterdir()) == []


async def test_main_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    requests: list[httpx.Request] = []
    fake = FakeClock()
    monkeypatch.setattr(
        spike, "make_client", lambda: httpx.AsyncClient(transport=_transport(requests))
    )
    real_throttle = spike.Throttle
    monkeypatch.setattr(
        spike,
        "Throttle",
        lambda rps: real_throttle(rps, clock=fake.clock, sleep=fake.sleep),
    )
    assert await spike.amain(["--out-dir", str(tmp_path), "--rps", "2"]) == 0
    out = capsys.readouterr().out
    # Other tests may route structlog to stdout; the report is the JSON object that starts a line.
    report = json.loads(out[out.index("{\n") :])
    assert report["versions"]["records"] == RECORDED_VERSIONS
    assert fake.sleeps == [pytest.approx(0.5)] * 6


def test_main_runs_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_amain(argv: list[str] | None) -> int:
        return 0

    monkeypatch.setattr(spike, "amain", fake_amain)
    assert spike.main([]) == 0
