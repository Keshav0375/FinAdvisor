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
import structlog

from scripts import spike_fca_api as spike
from src.config import Settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fca_api"
TODAY = date(2026, 11, 21)
# Never contacted: every live test uses a mock transport.
BASE = "https://handbook.fca.org.uk"
DEMO_KEY = spike.DEMO_FCA_API_KEY
LIVE_KEY = "unit-live-key"


def read(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def demo_client() -> httpx.AsyncClient:
    return spike.make_client(spike.build_demo_transport(spike.AUTH_HEADER_DEFAULT, TODAY))


async def no_sleep(_seconds: float) -> None:
    return None


def fast_throttle(rps: float = 1.0) -> spike.Throttle:
    return spike.Throttle(rps, sleep=no_sleep)


def settings(**overrides: Any) -> spike.FcaSpikeSettings:
    return spike.FcaSpikeSettings(**overrides)


async def run_demo(**overrides: Any) -> dict[str, Any]:
    async with demo_client() as client:
        return await spike.run(
            client,
            fast_throttle(),
            settings(**overrides),
            base_url=spike.DEMO_BASE_URL,
            auth_header=spike.AUTH_HEADER_DEFAULT,
            module_path=spike.MODULE_PATH_DEFAULT,
            today=TODAY,
            out_dir=None,
        )


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


async def run_live(
    transport: httpx.MockTransport, out_dir: Path | None = None, **overrides: Any
) -> dict[str, Any]:
    async with spike.make_client(transport) as client:
        return await spike.run(
            client,
            fast_throttle(),
            settings(mode="live", key=LIVE_KEY, **overrides),
            base_url=BASE,
            auth_header="X-Test-Key",
            module_path=spike.MODULE_PATH_DEFAULT,
            today=TODAY,
            out_dir=out_dir,
        )


def live_env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("FCA_API_MODE", "live")
    monkeypatch.setenv("FCA_API_KEY", LIVE_KEY)
    monkeypatch.setenv("FCA_API_RATE_LIMIT_RPS", "1000")
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


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


def test_global_config_has_only_the_rate_knob() -> None:
    # The spike's mode, key and the rest are spike-local (E11-T20 owns them), not app config.
    example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
    assert "FCA_API_MODE" not in example
    assert "FCA_API_KEY" not in example


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
    assert rate["requests"] == 10
    assert rate["rps"] == 1.0
    assert rate["statement"] == "none reached after 10 requests at 1.0 rps"
    assert report["requests"] == 5 + 10
    assert len(report["probes"]) == report["requests"]
    assert [p["label"] for p in report["probes"][3:5]] == ["revoked login", "suspended login"]


async def test_run_uses_the_burst_and_past_date_knobs() -> None:
    report = await run_demo(burst_requests=3, past_date_offset_days=1)
    assert report["rate"]["requests"] == 3
    assert report["past_date"]["date"] == "2026-11-20"
    assert report["requests"] == 5 + 3


async def test_demo_main_does_not_write_samples(tmp_path: Path) -> None:
    assert await spike.amain(["--out-dir", str(tmp_path)]) == 0
    assert list(tmp_path.iterdir()) == []


async def test_main_logs_to_stderr_so_stdout_stays_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await spike.amain([])
    out = capsys.readouterr()
    assert "probe" in out.err
    json.loads(out.out)


@pytest.mark.parametrize(
    ("path", "params", "key", "status"),
    [
        ("/handbook/COBS/9A", {}, DEMO_KEY, 200),
        ("/handbook/COBS/6", {"date": "2026-11-21"}, DEMO_KEY, 200),
        ("/handbook/COBS/9A", {"date": "2026-12-31"}, DEMO_KEY, 200),
        ("/handbook/COBS/9A", {"date": "2026-11-20"}, DEMO_KEY, 400),
        ("/handbook/COBS/9A", {}, spike.DEMO_REVOKED_KEY, 401),
        ("/handbook/COBS/9A", {}, spike.DEMO_SUSPENDED_KEY, 403),
        ("/handbook/COBS/9A", {}, "some-other-key", 401),
        ("/handbook/COBS/9A", {}, None, 401),
        ("/handbook/COBS/1", {}, DEMO_KEY, 404),
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
    headers = {spike.AUTH_HEADER_DEFAULT: DEMO_KEY}
    async with demo_client() as client:
        ok = await client.get(f"{spike.DEMO_BASE_URL}/handbook/COBS/9A", headers=headers)
        past = await client.get(
            f"{spike.DEMO_BASE_URL}/handbook/COBS/9A",
            params={"date": "2020-01-01"},
            headers=headers,
        )
    assert ok.json() == read("cobs_9a_module.json")
    assert past.json() == read("past_date_error.json")


async def test_run_reports_first_429_and_retry_after_and_stops_the_burst() -> None:
    # Live sends 4 fixed probes; the burst starts at request 5 and 429s from its 3rd (request 7).
    transport, seen = counting_transport(lambda n: 429 if n >= 7 else 200, retry_after="7")
    report = await run_live(transport)

    rate = report["rate"]
    assert rate["limited"] is True
    assert rate["first_429_at"] == 3
    assert rate["retry_after"] == "7"
    assert rate["requests"] == 3
    assert rate["statement"] == "limited at request 3 of the burst at 1.0 rps (Retry-After 7)"
    assert len(seen) == 7
    assert report["demo"] is False
    assert report["source"] == "live"


async def test_live_labels_the_auth_probe_as_an_unknown_key_response() -> None:
    transport, seen = counting_transport(lambda n: 401 if n == 4 else 200)
    report = await run_live(transport, burst_requests=1)

    assert report["auth"] == {"unknown_key": 401}
    assert "revoked" not in report["auth"] and "suspended" not in report["auth"]
    labels = [p["label"] for p in report["probes"]]
    assert labels[3] == "unknown-key response (no revoked key available)"
    assert "revoked login" not in labels and "suspended login" not in labels
    assert seen[3].headers["X-Test-Key"] == spike.UNKNOWN_KEY
    assert report["requests"] == 4 + 1


async def test_live_run_sends_the_key_and_a_user_agent_and_records_samples(
    tmp_path: Path,
) -> None:
    transport, seen = counting_transport(lambda n: 200)
    report = await run_live(transport, out_dir=tmp_path)

    assert all(r.headers["X-Test-Key"] == LIVE_KEY for r in seen[:3])
    assert all(r.headers["User-Agent"] == spike.USER_AGENT for r in seen)
    assert {p.name for p in tmp_path.iterdir()} == {"cobs_9a_module.json", "cobs_6_module.json"}
    assert json.loads((tmp_path / "cobs_9a_module.json").read_text(encoding="utf-8")) == read(
        "cobs_9a_module.json"
    )
    assert LIVE_KEY not in json.dumps(report)


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


def test_settings_defaults() -> None:
    config = settings()
    assert (config.mode, config.rate_limit_rps) == ("demo", 1.0)
    assert config.key.get_secret_value() == DEMO_KEY == "demo-fca-key"
    assert (config.timeout_s, config.burst_requests, config.past_date_offset_days) == (30.0, 10, 30)
    assert config.base_url is None


def test_settings_default_rate_is_the_global_settings_default() -> None:
    assert settings().rate_limit_rps == Settings.model_fields["fca_api_rate_limit_rps"].default


def test_load_settings_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    live_env(
        monkeypatch,
        FCA_API_RATE_LIMIT_RPS="0.5",
        FCA_API_TIMEOUT_S="5",
        FCA_API_BURST_REQUESTS="2",
        FCA_API_PAST_DATE_OFFSET_DAYS="7",
        FCA_API_BASE_URL=BASE,
    )
    config = spike.load_settings()
    assert (config.mode, config.rate_limit_rps) == ("live", 0.5)
    assert (config.timeout_s, config.burst_requests, config.past_date_offset_days) == (5.0, 2, 7)
    assert config.base_url == BASE
    assert config.key.get_secret_value() == LIVE_KEY
    assert LIVE_KEY not in repr(config)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FCA_API_RATE_LIMIT_RPS", "0"),
        ("FCA_API_RATE_LIMIT_RPS", "abc"),
        ("FCA_API_MODE", "sandbox"),
        ("FCA_API_TIMEOUT_S", "0"),
        ("FCA_API_BURST_REQUESTS", "0"),
        ("FCA_API_PAST_DATE_OFFSET_DAYS", "-1"),
    ],
)
def test_load_settings_rejects_a_bad_value_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        spike.load_settings()


@pytest.mark.parametrize("key", [DEMO_KEY, "REPLACE_ME", " ", "changeme"])
def test_live_rejects_the_demo_key_blank_and_placeholders(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setenv("FCA_API_MODE", "live")
    monkeypatch.setenv("FCA_API_KEY", key)
    with pytest.raises(ValueError, match="FCA_API_KEY") as exc:
        spike.load_settings()
    assert key.strip() == "" or key not in str(exc.value)


def test_demo_accepts_the_demo_key_and_rejects_a_blank_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FCA_API_KEY", DEMO_KEY)
    assert spike.load_settings().mode == "demo"
    monkeypatch.setenv("FCA_API_KEY", " ")
    with pytest.raises(ValueError, match="FCA_API_KEY"):
        spike.load_settings()


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


@pytest.mark.parametrize(
    "url",
    [
        "https://handbook.fca.org.uk",
        "https://handbook.fca.org.uk/api/v1",
        "https://fca.org.uk",
        "https://api.handbook.fca.org.uk:8443",
    ],
)
def test_live_accepts_fca_hosts(url: str) -> None:
    spike.validate_base_url("live", url, False)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid",
        "https://fca.org.uk.evil.example",
        "https://evilfca.org.uk",
        "https://fca.org.uk@evil.example",
    ],
)
def test_live_refuses_other_hosts_unless_allowed(url: str) -> None:
    with pytest.raises(ValueError, match="host|credentials"):
        spike.validate_base_url("live", url, False)


def test_live_allow_any_host_flag_lets_another_host_through() -> None:
    spike.validate_base_url("live", "https://example.invalid", True)


def test_live_base_url_must_be_present_https_and_without_credentials() -> None:
    with pytest.raises(ValueError, match="--base-url"):
        spike.validate_base_url("live", None, False)
    with pytest.raises(ValueError, match="https"):
        spike.validate_base_url("live", "http://handbook.fca.org.uk", False)
    with pytest.raises(ValueError, match="credentials"):
        spike.validate_base_url("live", "https://user:pw@handbook.fca.org.uk", True)
    with pytest.raises(ValueError, match="valid URL"):
        spike.validate_base_url("live", "https://[invalid", True)


def test_demo_needs_no_base_url() -> None:
    spike.validate_base_url("demo", None, False)


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


async def test_main_live_refuses_a_non_fca_host_without_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live_env(monkeypatch)
    transport, seen = counting_transport(lambda n: 200)
    with pytest.raises(SystemExit) as exc:
        await spike.amain(["--base-url", "https://example.invalid"], live_transport=transport)
    assert exc.value.code == 2
    assert "--allow-any-host" in capsys.readouterr().err
    assert seen == []


async def test_main_live_allow_any_host_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    live_env(monkeypatch)
    transport, seen = counting_transport(lambda n: 200)
    code = await spike.amain(
        ["--base-url", "https://example.invalid", "--allow-any-host", "--out-dir", str(tmp_path)],
        live_transport=transport,
    )
    assert code == 0
    assert str(seen[0].url).startswith("https://example.invalid/handbook/COBS/9A")
    assert json.loads(capsys.readouterr().out)["demo"] is False


async def test_main_live_takes_the_base_url_from_the_settings_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    live_env(monkeypatch, FCA_API_BASE_URL=BASE, FCA_API_BURST_REQUESTS="1")
    transport, seen = counting_transport(lambda n: 200)
    code = await spike.amain(["--out-dir", str(tmp_path)], live_transport=transport)
    assert code == 0
    assert str(seen[0].url).startswith(BASE)
    assert len(seen) == 4 + 1


async def test_live_run_never_leaks_the_key_into_report_output_or_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    live_env(monkeypatch)
    # A 429 mid-burst and an unknown-key 401 exercise the less common paths too.
    transport, _ = counting_transport(lambda n: 401 if n == 4 else 429 if n >= 6 else 200)

    with structlog.testing.capture_logs() as logs:
        code = await spike.amain(
            ["--base-url", BASE, "--out-dir", str(tmp_path)], live_transport=transport
        )
    out = capsys.readouterr()

    assert code == 0
    assert logs, "the probes must be logged"
    assert LIVE_KEY not in json.dumps(logs, default=str)
    assert LIVE_KEY not in out.out + out.err
    assert LIVE_KEY not in json.dumps(json.loads(out.out))
    for sample in tmp_path.iterdir():
        assert LIVE_KEY not in sample.read_text(encoding="utf-8")


async def test_a_rejected_key_is_not_echoed_in_the_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FCA_API_MODE", "live")
    monkeypatch.setenv("FCA_API_KEY", LIVE_KEY)
    monkeypatch.setenv("FCA_API_TIMEOUT_S", "0")
    with pytest.raises(SystemExit):
        await spike.amain(["--base-url", BASE])
    assert LIVE_KEY not in capsys.readouterr().err


async def test_main_transport_error_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    code = await spike.amain(["--base-url", BASE], live_transport=httpx.MockTransport(handler))
    out = capsys.readouterr()
    assert code == 1
    assert LIVE_KEY not in out.out + out.err


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
