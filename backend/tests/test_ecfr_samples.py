from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import spike_ecfr_api as spike

BACKEND_DIR = Path(__file__).resolve().parent.parent
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
    # Counted straight from the recorded file, not through the spike helper under test.
    records = versions["content_versions"]
    removed = [r for r in records if r["removed"] is True]
    non_substantive = [r for r in records if r["substantive"] is False]
    assert len(records) == RECORDED_VERSIONS
    assert versions["meta"]["result_count"] == str(len(records))
    assert len(removed) == RECORDED_REMOVED
    assert len(non_substantive) == RECORDED_NON_SUBSTANTIVE
    # Next to the arch figures: removed matches, the version count drifted by 2.
    assert len(removed) == ARCH_REMOVED
    assert len(records) - ARCH_VERSIONS == 2
    assert titles["meta"]["date"] == RECORDED_UP_TO_DATE_AS_OF
    assert spike.title_entry(titles, 17)["up_to_date_as_of"] == RECORDED_UP_TO_DATE_AS_OF


def test_summarise_versions_matches_the_recorded_sample() -> None:
    summary = spike.summarise_versions(load("versions_title-17_part-275.json"))
    assert (summary["records"], summary["removed"], summary["non_substantive"]) == (
        RECORDED_VERSIONS,
        RECORDED_REMOVED,
        RECORDED_NON_SUBSTANTIVE,
    )


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


def test_rate_headers_picks_only_limit_headers() -> None:
    headers = httpx.Headers(
        {"X-RateLimit-Remaining": "5", "Retry-After": "2", "Cache-Control": "no-store"}
    )
    assert spike.rate_headers(headers) == {"x-ratelimit-remaining": "5", "retry-after": "2"}


def test_configured_rps_defaults_to_the_settings_field_default() -> None:
    assert spike.configured_rps({}) == 1.0


def test_configured_rps_reads_the_env_variable() -> None:
    assert spike.configured_rps({"CONNECTOR_RATE_RPS_ECFR": "0.5"}) == 0.5


def test_configured_rps_defaults_to_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_RATE_RPS_ECFR", "0.25")
    assert spike.configured_rps() == 0.25


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_configured_rps_rejects_what_settings_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="CONNECTOR_RATE_RPS_ECFR"):
        spike.configured_rps({"CONNECTOR_RATE_RPS_ECFR": value})


def test_resolve_rps_uses_configured_when_not_requested() -> None:
    assert spike.resolve_rps(None, 1.0, False) == 1.0


def test_resolve_rps_allows_lower_or_equal() -> None:
    assert spike.resolve_rps(0.5, 1.0, False) == 0.5
    assert spike.resolve_rps(1.0, 1.0, False) == 1.0


def test_resolve_rps_refuses_above_the_limit() -> None:
    with pytest.raises(ValueError, match="--allow-above-limit"):
        spike.resolve_rps(2.0, 1.0, False)


def test_resolve_rps_allows_above_the_limit_when_forced() -> None:
    assert spike.resolve_rps(2.0, 1.0, True) == 2.0


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
    monkeypatch.delenv("CONNECTOR_RATE_RPS_ECFR", raising=False)
    argv = ["--out-dir", str(tmp_path), "--rps", "2", "--allow-above-limit"]
    assert await spike.amain(argv) == 0
    out = capsys.readouterr().out
    # Other tests may route structlog to stdout; the report is the JSON object that starts a line.
    report = json.loads(out[out.index("{\n") :])
    assert report["versions"]["records"] == RECORDED_VERSIONS
    assert fake.sleeps == [pytest.approx(0.5)] * 6


async def test_main_default_rate_is_the_configured_knob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeClock()
    monkeypatch.setattr(spike, "make_client", lambda: httpx.AsyncClient(transport=_transport([])))
    real_throttle = spike.Throttle
    monkeypatch.setattr(
        spike, "Throttle", lambda rps: real_throttle(rps, clock=fake.clock, sleep=fake.sleep)
    )
    monkeypatch.setenv("CONNECTOR_RATE_RPS_ECFR", "4")
    assert await spike.amain(["--out-dir", str(tmp_path)]) == 0
    assert fake.sleeps == [pytest.approx(0.25)] * 6


async def test_main_refuses_a_rate_above_the_knob_without_the_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        spike, "make_client", lambda: httpx.AsyncClient(transport=_transport(requests))
    )
    monkeypatch.delenv("CONNECTOR_RATE_RPS_ECFR", raising=False)
    with pytest.raises(SystemExit) as exc:
        await spike.amain(["--out-dir", str(tmp_path), "--rps", "5"])
    assert exc.value.code == 2
    assert requests == []


def test_documented_command_runs_from_backend() -> None:
    # The documented way (docs/spikes/ecfr-api.md): `python -m scripts.spike_ecfr_api` in backend/.
    result = subprocess.run(
        [sys.executable, "-m", "scripts.spike_ecfr_api", "--help"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--allow-above-limit" in result.stdout


def test_main_runs_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_amain(argv: list[str] | None) -> int:
        return 0

    monkeypatch.setattr(spike, "amain", fake_amain)
    assert spike.main([]) == 0
