"""Settings for the stub chat-api, read once from environment variables.

Every tunable value lives here with a default. An invalid value raises ConfigError at startup.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(ValueError):
    """An environment variable holds a value the stub cannot use."""


def _int(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _str(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    return raw


@dataclass(frozen=True)
class Settings:
    host: str = "0.0.0.0"  # the stub listens inside a container or a test network
    port: int = 8080
    sse_event_name: str = "tick"
    sse_max_events: int = 1000
    sse_max_interval_ms: int = 5000
    sse_max_duration_s: int = 60
    metadata_email: str = "stub@fa-gateway.iam.gserviceaccount.com"
    metadata_token_ttl_s: int = 3600
    keepalive_timeout_s: int = 5
    max_audience_length: int = 2048

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        defaults = cls()
        return cls(
            host=_str(source, "STUB_HOST", defaults.host),
            port=_int(source, "STUB_PORT", defaults.port, 1),
            sse_event_name=_str(source, "STUB_SSE_EVENT_NAME", defaults.sse_event_name),
            sse_max_events=_int(source, "STUB_SSE_MAX_EVENTS", defaults.sse_max_events, 1),
            sse_max_interval_ms=_int(
                source, "STUB_SSE_MAX_INTERVAL_MS", defaults.sse_max_interval_ms, 0
            ),
            sse_max_duration_s=_int(
                source, "STUB_SSE_MAX_DURATION_S", defaults.sse_max_duration_s, 1
            ),
            metadata_email=_str(source, "STUB_METADATA_EMAIL", defaults.metadata_email),
            metadata_token_ttl_s=_int(
                source, "STUB_METADATA_TOKEN_TTL_S", defaults.metadata_token_ttl_s, 1
            ),
            keepalive_timeout_s=_int(
                source, "STUB_KEEPALIVE_TIMEOUT_S", defaults.keepalive_timeout_s, 1
            ),
            max_audience_length=_int(
                source, "STUB_METADATA_MAX_AUDIENCE_LENGTH", defaults.max_audience_length, 1
            ),
        )
