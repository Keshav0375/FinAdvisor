import pytest

from config import ConfigError, Settings


def test_defaults_without_env():
    s = Settings.from_env({})
    assert s == Settings()
    assert s.port == 8080
    assert s.sse_event_name == "tick"


def test_from_env_reads_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("STUB_PORT", "9191")
    assert Settings.from_env().port == 9191


def test_overrides_every_knob():
    s = Settings.from_env(
        {
            "STUB_HOST": "127.0.0.1",
            "STUB_PORT": "9000",
            "STUB_SSE_EVENT_NAME": "beat",
            "STUB_SSE_MAX_EVENTS": "5",
            "STUB_SSE_MAX_INTERVAL_MS": "0",
            "STUB_SSE_MAX_DURATION_S": "2",
            "STUB_METADATA_EMAIL": "x@y",
            "STUB_METADATA_TOKEN_TTL_S": "10",
            "STUB_KEEPALIVE_TIMEOUT_S": "3",
        }
    )
    assert s == Settings(
        host="127.0.0.1",
        port=9000,
        sse_event_name="beat",
        sse_max_events=5,
        sse_max_interval_ms=0,
        sse_max_duration_s=2,
        metadata_email="x@y",
        metadata_token_ttl_s=10,
        keepalive_timeout_s=3,
    )


def test_empty_value_uses_default():
    assert Settings.from_env({"STUB_PORT": ""}).port == 8080


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STUB_PORT", "abc"),
        ("STUB_PORT", "0"),
        ("STUB_SSE_MAX_EVENTS", "0"),
        ("STUB_SSE_MAX_INTERVAL_MS", "-1"),
        ("STUB_SSE_MAX_DURATION_S", "0"),
        ("STUB_METADATA_TOKEN_TTL_S", "0"),
        ("STUB_KEEPALIVE_TIMEOUT_S", "x"),
    ],
)
def test_invalid_value_fails_fast_naming_the_variable(name, value):
    with pytest.raises(ConfigError, match=name):
        Settings.from_env({name: value})
