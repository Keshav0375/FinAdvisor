from __future__ import annotations

import gzip
import io
import json
import os
import urllib.error
import urllib.request
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider

from scripts import spike_langfuse_v5 as spike
from scripts.spike_langfuse_v5 import (
    INPUT_KEY,
    MARKER,
    NORMAL_SPAN,
    OUTPUT_KEY,
    REDACTED,
    SENSITIVE_SPAN,
    SpikeAuthError,
    SpikeError,
    evaluate_trace,
    fetch_trace,
    is_complete,
    main,
    mask_otel_spans,
    parse_args,
    poll_trace,
    read_credentials,
    redact_marker,
)


def _trace(normal_input: Any = None, sensitive_input: Any = None, sensitive_output: Any = None):
    return {
        "id": "t1",
        "input": None,
        "output": None,
        "observations": [
            {"name": NORMAL_SPAN, "input": normal_input, "output": None},
            {"name": SENSITIVE_SPAN, "input": sensitive_input, "output": sensitive_output},
        ],
    }


class _Span:
    def __init__(self, attributes: dict[str, Any]) -> None:
        self.attributes = MappingProxyType(attributes)


class _Params:
    def __init__(self, spans: dict[str, _Span]) -> None:
        self.spans = spans


# --- masking (no network) -------------------------------------------------------------------


def test_redact_marker_replaces_marker_and_leaves_other_values() -> None:
    assert redact_marker(f"ssn {MARKER} end") == f"ssn {REDACTED} end"
    assert redact_marker("harmless") == "harmless"
    assert redact_marker(42) == 42


def test_mask_otel_spans_patches_only_input_and_output_with_marker() -> None:
    params = _Params(
        {
            "leaky": _Span({INPUT_KEY: json.dumps({"text": MARKER}), OUTPUT_KEY: f"echo {MARKER}"}),
            "clean": _Span({INPUT_KEY: "nothing", "other": MARKER}),
            "empty": _Span({}),
        }
    )
    result = mask_otel_spans(params=params)
    assert set(result.span_patches) == {"leaky"}
    patch_ = result.span_patches["leaky"]
    assert set(patch_.set_attributes) == {INPUT_KEY, OUTPUT_KEY}
    assert MARKER not in json.dumps(dict(patch_.set_attributes))
    assert REDACTED in patch_.set_attributes[INPUT_KEY]


# --- evaluation ----------------------------------------------------------------------------


def test_evaluate_trace_passes_for_masked_trace_with_empty_sensitive_span() -> None:
    assert evaluate_trace(_trace(normal_input=f'{{"text": "{REDACTED}"}}')) == []


def test_evaluate_trace_fails_on_marker_in_any_observation() -> None:
    problems = evaluate_trace(_trace(normal_input=f"ssn {MARKER}"))
    assert any("SPIKE-SSN" in p and NORMAL_SPAN in p for p in problems)


@pytest.mark.parametrize("field", ["input", "output"])
def test_evaluate_trace_fails_on_non_empty_sensitive_field(field: str) -> None:
    kwargs = {"sensitive_input": "x"} if field == "input" else {"sensitive_output": "x"}
    problems = evaluate_trace(_trace(**kwargs))
    assert f"sensitive span has non-empty {field}" in problems


def test_evaluate_trace_fails_on_missing_observation_and_trace_level_leak() -> None:
    trace = {"input": MARKER, "output": None, "observations": [{"name": NORMAL_SPAN}]}
    problems = evaluate_trace(trace)
    assert f"observation '{SENSITIVE_SPAN}' missing from trace" in problems
    assert "SPIKE-SSN found in trace input" in problems
    assert evaluate_trace({}) != []


def test_is_complete_needs_both_spans() -> None:
    assert is_complete(_trace())
    assert not is_complete({"observations": [{"name": NORMAL_SPAN}]})
    assert not is_complete({})


# --- polling -------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_poll_trace_returns_once_complete() -> None:
    clock = _Clock()
    responses = iter([None, {"observations": []}, _trace()])
    result = poll_trace(
        lambda: next(responses), timeout=60, interval=3, sleep=clock.sleep, clock=clock
    )
    assert result == _trace()
    assert clock.now == 6


def test_poll_trace_returns_last_partial_trace_at_deadline() -> None:
    clock = _Clock()
    partial = {"observations": [{"name": NORMAL_SPAN}]}
    result = poll_trace(lambda: partial, timeout=10, interval=5, sleep=clock.sleep, clock=clock)
    assert result == partial


def test_poll_trace_returns_none_when_never_found() -> None:
    clock = _Clock()
    assert poll_trace(lambda: None, timeout=4, interval=2, sleep=clock.sleep, clock=clock) is None


def test_poll_trace_raises_auth_error_immediately() -> None:
    clock = _Clock()

    def fetch() -> dict[str, Any] | None:
        raise SpikeAuthError("Langfuse API rejected the key pair (HTTP 401)")

    with pytest.raises(SpikeAuthError, match="rejected"):
        poll_trace(fetch, timeout=60, interval=3, sleep=clock.sleep, clock=clock)
    assert clock.now == 0


def test_poll_trace_retries_transport_error_then_raises_at_deadline() -> None:
    clock = _Clock()
    calls: list[int] = []

    def fetch() -> dict[str, Any] | None:
        calls.append(1)
        raise SpikeError("transport error: URLError")

    with pytest.raises(SpikeError, match="transport"):
        poll_trace(fetch, timeout=6, interval=3, sleep=clock.sleep, clock=clock)
    assert len(calls) == 3


def test_poll_trace_recovers_from_transport_error() -> None:
    clock = _Clock()
    state = {"n": 0}

    def fetch() -> dict[str, Any] | None:
        state["n"] += 1
        if state["n"] == 1:
            raise SpikeError("transport error: URLError")
        return _trace()

    assert poll_trace(fetch, timeout=60, interval=3, sleep=clock.sleep, clock=clock) == _trace()


# --- HTTP fetch (urlopen mocked: no network) -----------------------------------------------


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://h", code, "msg", None, io.BytesIO(b""))  # type: ignore[arg-type]


def _urlopen_returning(body: bytes) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value = io.BytesIO(body)
    return MagicMock(return_value=response)


def test_fetch_trace_returns_body_and_sends_basic_auth() -> None:
    urlopen = _urlopen_returning(b'{"id": "t1"}')
    with patch.object(spike.urllib.request, "urlopen", urlopen):
        assert fetch_trace("https://h.example/", "pk", "sk", "t1", 5) == {"id": "t1"}
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://h.example/api/public/traces/t1"
    assert request.get_header("Authorization").startswith("Basic ")
    assert urlopen.call_args.kwargs["timeout"] == 5


def test_fetch_trace_404_is_not_found_yet() -> None:
    with patch.object(spike.urllib.request, "urlopen", side_effect=_http_error(404)):
        assert fetch_trace("https://h.example", "pk", "sk", "t1", 5) is None


@pytest.mark.parametrize("code", [401, 403])
def test_fetch_trace_auth_failure_raises_without_leaking_keys(code: int) -> None:
    with (
        patch.object(spike.urllib.request, "urlopen", side_effect=_http_error(code)),
        pytest.raises(SpikeAuthError, match="rejected the key pair") as exc,
    ):
        fetch_trace("https://h.example", "pk-secret-1", "sk-secret-2", "t1", 5)
    assert "secret" not in str(exc.value)


def test_fetch_trace_other_errors_raise() -> None:
    with (
        patch.object(spike.urllib.request, "urlopen", side_effect=_http_error(500)),
        pytest.raises(SpikeError, match="HTTP 500"),
    ):
        fetch_trace("https://h.example", "pk", "sk", "t1", 5)
    with (
        patch.object(spike.urllib.request, "urlopen", side_effect=urllib.error.URLError("x")),
        pytest.raises(SpikeError, match="transport"),
    ):
        fetch_trace("https://h.example", "pk", "sk", "t1", 5)
    with (
        patch.object(spike.urllib.request, "urlopen", _urlopen_returning(b"[1]")),
        pytest.raises(SpikeError, match="unexpected"),
    ):
        fetch_trace("https://h.example", "pk", "sk", "t1", 5)


def test_fetch_trace_rejects_non_http_scheme() -> None:
    with pytest.raises(SpikeError, match="http"):
        fetch_trace("file:///etc", "pk", "sk", "t1", 5)


# --- config, args, main --------------------------------------------------------------------


def test_read_credentials_lists_missing_names_only() -> None:
    with pytest.raises(SpikeError) as exc:
        read_credentials({"LANGFUSE_HOST": "https://h", "LANGFUSE_PUBLIC_KEY": "pk-value"})
    assert "LANGFUSE_SECRET_KEY" in str(exc.value)
    assert "pk-value" not in str(exc.value)
    assert read_credentials(
        {"LANGFUSE_HOST": "h", "LANGFUSE_PUBLIC_KEY": "p", "LANGFUSE_SECRET_KEY": "s"}
    ) == ("h", "p", "s")


def test_parse_args_defaults_and_no_mask() -> None:
    args = parse_args([])
    assert not args.demo
    assert not args.no_mask
    assert args.poll_timeout == spike.DEFAULT_POLL_TIMEOUT_S
    assert parse_args(["--live"]).live
    assert parse_args(["--no-mask"]).no_mask


def test_parse_args_live_is_the_default_and_modes_exclude_each_other() -> None:
    assert parse_args(["--demo"]).demo
    with pytest.raises(SystemExit):
        parse_args(["--demo", "--live"])


ENV = {"LANGFUSE_HOST": "https://h.example", "LANGFUSE_PUBLIC_KEY": "p", "LANGFUSE_SECRET_KEY": "s"}


def _run_main(
    monkeypatch: pytest.MonkeyPatch, trace: dict[str, Any] | None, argv: list[str] | None = None
) -> int:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(spike, "run_spike", lambda *a, **k: "trace-1")
    monkeypatch.setattr(spike, "fetch_trace", lambda *a, **k: trace)
    return main([*(argv or []), "--poll-timeout", "0"])


def test_main_exit_0_and_prints_trace_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run_main(monkeypatch, _trace(normal_input=REDACTED)) == 0
    assert "trace-1" in capsys.readouterr().out


def test_main_exit_1_on_leak_as_with_no_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, _trace(normal_input=MARKER), ["--no-mask"]) == 1


def test_main_exit_1_when_trace_not_retrievable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, None) == 1


def test_main_exit_2_on_missing_env_and_auth_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert main([]) == 2
    err = capsys.readouterr().err
    assert err.startswith("ERROR: missing environment variables: LANGFUSE_HOST")
    assert "--demo" in err
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(spike, "run_spike", lambda *a, **k: "trace-1")

    def reject(*a: Any, **k: Any) -> None:
        raise SpikeAuthError("Langfuse API rejected the key pair (HTTP 401)")

    monkeypatch.setattr(spike, "fetch_trace", reject)
    assert main(["--poll-timeout", "0"]) == 2
    assert "rejected" in capsys.readouterr().err


def test_run_spike_builds_client_with_masking_and_returns_trace_id() -> None:
    """Langfuse is faked: this checks the wiring (mask hook, base_url), not the cloud."""
    fake_client = MagicMock()
    fake_client.get_current_trace_id.return_value = "abc123"
    with (
        patch("langfuse.Langfuse", return_value=fake_client) as ctor,
        patch("langfuse.observe", lambda **_kw: lambda fn: fn),
    ):
        assert spike.run_spike("https://h.example", "p", "s", mask=True) == "abc123"
        assert ctor.call_args.kwargs["mask_otel_spans"] is mask_otel_spans
        assert ctor.call_args.kwargs["base_url"] == "https://h.example"
        assert ctor.call_args.kwargs["tracer_provider"] is None
        spike.run_spike("https://h.example", "p", "s", mask=False)
        assert ctor.call_args.kwargs["mask_otel_spans"] is None
    fake_client.flush.assert_called()


def test_run_spike_raises_without_trace_id() -> None:
    fake_client = MagicMock()
    fake_client.get_current_trace_id.return_value = None
    with (
        patch("langfuse.Langfuse", return_value=fake_client),
        patch("langfuse.observe", lambda **_kw: lambda fn: fn),
        pytest.raises(SpikeError, match="no trace id"),
    ):
        spike.run_spike("https://h.example", "p", "s", mask=True)


# --- demo mode: real 5.1.0 SDK exporting to a local in-process OTLP receiver ----------------
# Loopback only, no account and no network. The SDK, the masking hook and the exporter are real.


def _post(url: str, body: bytes, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 (loopback sink)
        return response.read()


def test_otlp_sink_records_trace_bodies_and_ungzips_and_ignores_other_paths() -> None:
    with spike.OtlpSink() as sink:
        assert sink.url.startswith("http://127.0.0.1:")
        assert _post(sink.url + spike.OTLP_TRACES_PATH, b"plain") == b""
        gz = {"Content-Encoding": "gzip"}
        assert _post(sink.url + spike.OTLP_TRACES_PATH, gzip.compress(b"zipped"), gz) == b""
        assert _post(sink.url + "/api/public/other", b"ignored") == b"{}"
    assert sink.bodies == [b"plain", b"zipped"]


def test_spans_from_bodies_rejects_garbage() -> None:
    with pytest.raises(SpikeError, match="not a valid OTLP"):
        spike.spans_from_bodies([b"\xff\xff\xff"])


def test_evaluate_capture_flags_missing_masking_and_raw_leak() -> None:
    problems = spike.evaluate_capture([], [], mask=True)
    assert "masking function never ran before export" in problems
    no_mask = spike.evaluate_capture([], [], mask=False)
    assert "masking function never ran before export" not in no_mask
    assert f"observation '{NORMAL_SPAN}' missing from trace" in no_mask


def test_demo_real_sdk_masks_before_export_and_capture_off_sends_nothing() -> None:
    with spike.OtlpSink() as sink:
        try:
            spike.run_spike(
                sink.url,
                spike.DEMO_PUBLIC_KEY,
                spike.DEMO_SECRET_KEY,
                mask=True,
                tracer_provider=TracerProvider(),
            )
        finally:
            spike.reset_langfuse_clients()
        bodies = list(sink.bodies)
    by_name = {s["name"]: s["attributes"] for s in spike.spans_from_bodies(bodies)}
    assert {NORMAL_SPAN, SENSITIVE_SPAN} <= set(by_name)
    assert REDACTED in by_name[NORMAL_SPAN][INPUT_KEY]
    assert REDACTED in by_name[NORMAL_SPAN][OUTPUT_KEY]
    for key in (INPUT_KEY, OUTPUT_KEY):
        assert key not in by_name[SENSITIVE_SPAN]
    assert not any(spike.MARKER_PREFIX.encode() in body for body in bodies)


def test_run_demo_passes_with_masking_and_fails_without() -> None:
    assert spike.run_demo(mask=True) == []
    leaked = spike.run_demo(mask=False)
    assert any("raw exported payload" in problem for problem in leaked)
    assert any(NORMAL_SPAN in problem for problem in leaked)


def test_main_demo_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert main(["--demo"]) == 0
    assert "PASS (demo: local OTLP capture)" in capsys.readouterr().out
    assert main(["--demo", "--no-mask"]) == 1
    assert "FAIL:" in capsys.readouterr().out

    def broken(*, mask: bool) -> list[str]:
        raise SpikeError("captured export is not a valid OTLP request")

    monkeypatch.setattr(spike, "run_demo", broken)
    assert main(["--demo"]) == 2
    assert "not a valid OTLP" in capsys.readouterr().err


# --- live: the real Langfuse Cloud run (K2 keys); skipped unless LIVE_API_TESTS=1 -----------
# The conftest autouse fixture clears Settings keys per test, so snapshot them at import.

_LIVE_ENV = {name: os.environ.get(name, "") for name in ENV}


@pytest.mark.live
def test_live_cloud_accepts_sdk_5_with_masking_and_capture_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _LIVE_ENV.items():
        monkeypatch.setenv(name, value)
    assert main([]) == 0
