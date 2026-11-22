from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import spike_fca_api as spike
from src.config import DEMO_FCA_API_KEY, Settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fca_api"
TODAY = date(2026, 11, 21)
BASE = "https://fca.example.invalid"


def read(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def demo_client() -> httpx.AsyncClient:
    transport = spike.build_demo_transport(spike.AUTH_HEADER_DEFAULT, TODAY)
    return spike.make_client(transport)


async def no_sleep(_seconds: float) -> None:
    return None


def fast_throttle(rps: float = 1.0) -> spike.Throttle:
    return spike.Throttle(rps, sleep=no_sleep)


async def run_demo(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "mode": "demo",
        "base_url": spike.DEMO_BASE_URL,
        "auth_header": spike.AUTH_HEADER_DEFAULT,
        "key": DEMO_FCA_API_KEY,
        "module_path": spike.MODULE_PATH_DEFAULT,
        "today": TODAY,
        "out_dir": None,
    }
    kwargs.update(overrides)
    async with demo_client() as client:
        return await spike.run(client, fast_throttle(), **kwargs)


# AC2: the recorded module fixture parses with the sample loader.
def test_sample_loads() -> None:
    for name, reference in (("cobs_9a_module.json", "COBS 9A"), ("cobs_6_module.json", "COBS 6")):
        payload = spike.load_sample(name)
        module = spike.parse_module(payload)
        assert module.reference == reference
        assert len(module.provisions) >= 1
        assert module.last_modified <= module.version_date
        assert payload["_demo"].startswith("demo fixture, from docs")
        # Only placeholder text is stored, never FCA text.
        assert all("not FCA text" in p.text for p in module.provisions)


def test_sample_loader_path_matches_the_fixture_folder() -> None:
    assert spike.FIXTURES_DIR == FIXTURES


def test_every_fixture_is_marked_demo() -> None:
    names = sorted(p.name for p in FIXTURES.glob("*.json"))
    assert names == [
        "auth_error_401.json",
        "auth_error_403.json",
        "cobs_6_module.json",
        "cobs_9a_module.json",
        "past_date_error.json",
    ]
    for name in names:
        assert spike.load_sample(name)["_demo"].startswith("demo fixture, from docs")


def test_sample_without_demo_marker_rejected(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text('{"reference": "COBS 9A"}', encoding="utf-8")
    with pytest.raises(spike.SampleError, match="demo marker"):
        spike.load_sample("x.json", tmp_path)


def test_sample_that_is_not_an_object_rejected(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(spike.SampleError, match="JSON object"):
        spike.load_sample("x.json", tmp_path)


def test_parse_module_missing_field_rejected() -> None:
    payload = spike.load_sample("cobs_9a_module.json")
    del payload["version_date"]
    with pytest.raises(spike.SampleError, match="version_date"):
        spike.parse_module(payload)


# AC3: the config key exists, in Settings and in .env.example, with the justified default.
def test_fca_rate_limit_config_present() -> None:
    field = Settings.model_fields["fca_api_rate_limit_rps"]
    assert field.validation_alias == "FCA_API_RATE_LIMIT_RPS"
    assert field.default == 1.0
    example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
    lines = [ln for ln in example.splitlines() if ln.startswith("FCA_API_RATE_LIMIT_RPS=")]
    assert lines == ["FCA_API_RATE_LIMIT_RPS=1.0"]


def test_env_example_has_demo_defaults_and_no_real_key() -> None:
    example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
    assert "FCA_API_MODE=demo" in example
    assert f"FCA_API_KEY={DEMO_FCA_API_KEY}" in example


# AC1: the report names the format, the rate result and the past-date status.
async def test_demo_run_reports_format_rate_and_past_date() -> None:
    report = await run_demo()

    assert report["mode"] == "demo"
    assert report["demo"] is True
    assert report["source"] == "demo fixture, from docs"
    assert report["format"]["content_type"] == "application/json"
    assert report["format"]["parsed"] is True
    assert report["format"]["top_level_keys"] == [
        "_demo",
        "last_modified",
        "provisions",
        "reference",
        "title",
        "version_date",
    ]
    assert report["format"]["last_modified"] == "2026-10-01"
    assert report["last_modified_header"] == "Thu, 01 Oct 2026 00:00:00 GMT"
    assert report["past_date"] == {"date": "2026-10-22", "status": 400}
    assert report["auth"] == {"revoked": 401, "suspended": 403}
    rate = report["rate"]
    assert rate["limited"] is False
    assert rate["requests"] == spike.RATE_BURST_REQUESTS
    assert rate["rps"] == 1.0
    assert rate["statement"] == (
        f"none reached after {spike.RATE_BURST_REQUESTS} requests at 1.0 rps"
    )
    assert report["requests"] == 5 + spike.RATE_BURST_REQUESTS
    assert len(report["probes"]) == report["requests"]


async def test_demo_main_does_not_write_samples(tmp_path: Path) -> None:
    assert await spike.amain(["--out-dir", str(tmp_path)]) == 0
    assert list(tmp_path.iterdir()) == []


async def test_main_logs_to_stderr_so_stdout_stays_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await spike.amain([])
    out = capsys.readouterr()
    assert "probe" in out.err
    assert DEMO_FCA_API_KEY not in out.err
    json.loads(out.out)


@pytest.mark.parametrize(
    ("path", "params", "key", "status"),
    [
        ("/handbook/COBS/9A", {}, DEMO_FCA_API_KEY, 200),
        ("/handbook/COBS/6", {"date": "2026-11-21"}, DEMO_FCA_API_KEY, 200),
        ("/handbook/COBS/9A", {"date": "2026-12-31"}, DEMO_FCA_API_KEY, 200),
        ("/handbook/COBS/9A", {"date": "2026-11-20"}, DEMO_FCA_API_KEY, 400),
        ("/handbook/COBS/9A", {}, spike.DEMO_REVOKED_KEY, 401),
        ("/handbook/COBS/9A", {}, spike.DEMO_SUSPENDED_KEY, 403),
        ("/handbook/COBS/9A", {}, "some-other-key", 401),
        ("/handbook/COBS/9A", {}, None, 401),
        ("/handbook/COBS/1", {}, DEMO_FCA_API_KEY, 404),
    ],
)
async def test_demo_transport_statuses(
    path: str, params: dict[str, str], key: str | None, status: int
) -> None:
    headers = {} if key is None else {spike.AUTH_HEADER_DEFAULT: key}
    async with demo_client() as client:
        response = await client.get(f"{spike.DEMO_BASE_URL}{path}", params=params, headers=headers)
    assert response.status_code == status


async def test_demo_transport_serves_the_fixture_bodies() -> None:
    headers = {spike.AUTH_HEADER_DEFAULT: DEMO_FCA_API_KEY}
    async with demo_client() as client:
        ok = await client.get(f"{spike.DEMO_BASE_URL}/handbook/COBS/9A", headers=headers)
        past = await client.get(
            f"{spike.DEMO_BASE_URL}/handbook/COBS/9A",
            params={"date": "2020-01-01"},
            headers=headers,
        )
    assert ok.json() == read("cobs_9a_module.json")
    assert past.json() == read("past_date_error.json")


def counting_transport(
    statuses: Callable[[int], int], retry_after: str | None = None
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status = statuses(len(seen))
        headers = {"Retry-After": retry_after} if status == 429 and retry_after else {}
        body = read("cobs_9a_module.json") if status == 200 else {"error": "x"}
        return httpx.Response(status, json=body, headers=headers)

    return httpx.MockTransport(handler), seen


async def run_live(transport: httpx.MockTransport, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "mode": "live",
        "base_url": BASE,
        "auth_header": "X-Test-Key",
        "key": "unit-live-key",
        "module_path": spike.MODULE_PATH_DEFAULT,
        "today": TODAY,
        "out_dir": None,
    }
    kwargs.update(overrides)
    async with spike.make_client(transport) as client:
        return await spike.run(client, fast_throttle(), **kwargs)


async def test_run_reports_first_429_and_retry_after_and_stops_the_burst() -> None:
    # Requests 1 to 5 are the fixed probes; the burst starts at request 6 and 429s from its 3rd.
    transport, seen = counting_transport(lambda n: 429 if n >= 8 else 200, retry_after="7")
    report = await run_live(transport)

    rate = report["rate"]
    assert rate["limited"] is True
    assert rate["first_429_at"] == 3
    assert rate["retry_after"] == "7"
    assert rate["requests"] == 3
    assert rate["statement"] == "limited at request 3 of the burst at 1.0 rps (Retry-After 7)"
    assert len(seen) == 8
    assert report["demo"] is False
    assert report["source"] == "live"


async def test_live_run_sends_the_key_and_a_user_agent_and_records_samples(
    tmp_path: Path,
) -> None:
    transport, seen = counting_transport(lambda n: 200)
    report = await run_live(transport, out_dir=tmp_path)

    assert all(r.headers["X-Test-Key"] == "unit-live-key" for r in seen[:2])
    assert all(r.headers["User-Agent"] == spike.USER_AGENT for r in seen)
    assert {p.name for p in tmp_path.iterdir()} == {"cobs_9a_module.json", "cobs_6_module.json"}
    assert json.loads((tmp_path / "cobs_9a_module.json").read_text(encoding="utf-8")) == read(
        "cobs_9a_module.json"
    )
    assert "unit-live-key" not in json.dumps(report)


async def test_unparseable_format_is_recorded_not_raised() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": 1})

    report = await run_live(httpx.MockTransport(handler))
    assert report["format"]["parsed"] is False
    assert report["format"]["top_level_keys"] == ["unexpected"]
    assert report["format"]["last_modified"] is None
    assert report["last_modified_header"] is None


async def test_non_json_body_is_recorded_not_raised() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<xml/>", headers={"content-type": "application/xml"})

    report = await run_live(httpx.MockTransport(handler))
    assert report["format"] == {
        "content_type": "application/xml",
        "parsed": False,
        "top_level_keys": [],
        "last_modified": None,
    }


def test_resolve_config_defaults() -> None:
    config = spike.configured_fca({})
    assert (config.mode, config.rps) == ("demo", 1.0)
    assert config.key.get_secret_value() == DEMO_FCA_API_KEY


def test_resolve_config_reads_the_environment() -> None:
    config = spike.configured_fca(
        {"FCA_API_MODE": "live", "FCA_API_KEY": "unit-live-key", "FCA_API_RATE_LIMIT_RPS": "0.5"}
    )
    assert (config.mode, config.rps) == ("live", 0.5)
    assert config.key.get_secret_value() == "unit-live-key"
    assert "unit-live-key" not in repr(config)


def test_resolve_config_defaults_to_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FCA_API_RATE_LIMIT_RPS", "0.25")
    assert spike.configured_fca().rps == 0.25


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FCA_API_RATE_LIMIT_RPS", "0"),
        ("FCA_API_RATE_LIMIT_RPS", "abc"),
        ("FCA_API_MODE", "sandbox"),
    ],
)
def test_resolve_config_rejects_what_settings_rejects(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        spike.configured_fca({name: value})


def test_resolve_rps_uses_configured_when_not_requested() -> None:
    assert spike.resolve_rps(None, 1.0, False) == 1.0


def test_resolve_rps_allows_lower_or_equal() -> None:
    assert spike.resolve_rps(0.5, 1.0, False) == 0.5
    assert spike.resolve_rps(1.0, 1.0, False) == 1.0


def test_resolve_rps_refuses_above_the_limit() -> None:
    with pytest.raises(ValueError, match="FCA_API_RATE_LIMIT_RPS"):
        spike.resolve_rps(2.0, 1.0, False)


def test_resolve_rps_allows_above_the_limit_when_forced() -> None:
    assert spike.resolve_rps(2.0, 1.0, True) == 2.0


def test_live_needs_a_real_key_and_a_base_url() -> None:
    ok = {"mode": "live", "key": "unit-live-key", "base_url": "https://x.example.invalid"}
    spike.validate_target(**ok)
    with pytest.raises(ValueError, match="FCA_API_KEY"):
        spike.validate_target(**{**ok, "key": DEMO_FCA_API_KEY})
    with pytest.raises(ValueError, match="--base-url"):
        spike.validate_target(**{**ok, "base_url": None})
    with pytest.raises(ValueError, match="https"):
        spike.validate_target(**{**ok, "base_url": "http://x.example.invalid"})


def test_demo_needs_no_base_url_or_real_key() -> None:
    spike.validate_target(mode="demo", key=DEMO_FCA_API_KEY, base_url=None)


async def test_throttle_spaces_calls_at_least_one_interval() -> None:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    throttle = spike.Throttle(2.0, clock=lambda: now[0], sleep=sleep)
    await throttle.wait()
    await throttle.wait()
    now[0] += 0.2
    await throttle.wait()
    assert slept == pytest.approx([0.5, 0.3])


@pytest.mark.parametrize("rps", [0.0, -1.0])
def test_throttle_rejects_non_positive_rate(rps: float) -> None:
    with pytest.raises(ValueError, match="rps"):
        spike.Throttle(rps)


async def test_main_demo_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    code = await spike.amain([])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["demo"] is True
    assert report["past_date"]["status"] == 400


async def test_main_refuses_a_rate_above_the_knob(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        await spike.amain(["--rps", "5"])
    assert exc.value.code == 2
    assert "FCA_API_RATE_LIMIT_RPS" in capsys.readouterr().err


async def test_main_live_without_a_real_key_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FCA_API_MODE", "live")
    with pytest.raises(SystemExit) as exc:
        await spike.amain(["--base-url", BASE])
    assert exc.value.code == 2
    assert "FCA_API_KEY" in capsys.readouterr().err


async def test_main_live_runs_and_never_prints_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("FCA_API_MODE", "live")
    monkeypatch.setenv("FCA_API_KEY", "unit-live-key")
    monkeypatch.setenv("FCA_API_RATE_LIMIT_RPS", "1000")
    transport, _ = counting_transport(lambda n: 200)
    code = await spike.amain(
        ["--base-url", BASE, "--out-dir", str(tmp_path)], live_transport=transport
    )
    out = capsys.readouterr()
    assert code == 0
    assert json.loads(out.out)["demo"] is False
    assert "unit-live-key" not in out.out + out.err
    assert (tmp_path / "cobs_9a_module.json").exists()


async def test_main_transport_error_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FCA_API_MODE", "live")
    monkeypatch.setenv("FCA_API_KEY", "unit-live-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    code = await spike.amain(["--base-url", BASE], live_transport=httpx.MockTransport(handler))
    assert code == 1
    assert "unit-live-key" not in capsys.readouterr().out


def test_documented_command_runs_from_backend() -> None:
    # The command in docs/spikes/fca-handbook-api.md, demo mode: no network, no account.
    env = {k: v for k, v in os.environ.items() if not k.startswith("FCA_API_")}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.spike_fca_api"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["demo"] is True


def test_main_runs_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(argv: list[str] | None = None, **_: Any) -> int:
        return 7

    monkeypatch.setattr(spike, "amain", fake)
    assert spike.main([]) == 7
