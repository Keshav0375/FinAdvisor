from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that mean "not filled in". Compared case-insensitively against the stripped secret.
# REPLACE_ME is the placeholder used by .env.example.
PLACEHOLDER_SECRETS = frozenset({"replace_me", "changeme", "change-me", "placeholder", "xxx"})

# Security floor for the JWT signing secret; not an operator knob.
JWT_SECRET_MIN_LENGTH = 32

# Hosts where a plain-http LLM endpoint is allowed (local test servers); security floor, not a knob.
LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

MODEL_LIFECYCLE_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/model-deprecations"


@dataclass(frozen=True)
class ModelLifecycle:
    model: str
    deprecated_since: date | None = None
    retires: date | None = None
    source: str = MODEL_LIFECYCLE_SOURCE_URL


# Allowlist of known model ids for the LLM_MODEL_* settings below (ADR-025 R6: allowlist in
# config). An id absent from this table is an `unknown` problem (arch 06 section 6.6: unknown ids
# fail). Dates come from ADR-006 (decision-logs.md, accepted 2026-11-01), not re-verified live;
# the table is a standing maintenance cost (ADR-006, "Harder later"). The ADR gives no
# retirement date for claude-haiku-4-5, so it only warns. src/model_lifecycle.py holds the logic.
LIFECYCLE_TABLE: dict[str, ModelLifecycle] = {
    row.model: row
    for row in (
        ModelLifecycle("claude-sonnet-5-5"),
        ModelLifecycle("claude-haiku-5-5"),
        ModelLifecycle("claude-sonnet-4-6"),
        ModelLifecycle("claude-sonnet-4-5", retires=date(2026, 11, 30)),
        ModelLifecycle("claude-haiku-4-5", deprecated_since=date(2026, 10, 15)),
        ModelLifecycle("claude-sonnet-4-20250514", retires=date(2026, 6, 15)),
    )
}


def _env(name: str, **kwargs: Any) -> Any:
    """Field read from the upper-case environment variable `name`; errors name that variable."""
    return Field(validation_alias=name, **kwargs)


class Settings(BaseSettings):
    """The one typed config. No secret has a default; a missing required key fails boot."""

    # hide_input_in_errors keeps env values (secrets included) out of ValidationError text.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    env_profile: Literal["dev", "demo", "prod"] = _env("ENV_PROFILE", default="dev")

    # Database
    db_host: str = _env("DB_HOST")
    db_port: int = _env("DB_PORT", default=5432)
    db_name: str = _env("DB_NAME")
    db_user: str = _env("DB_USER")
    db_password: SecretStr = _env("DB_PASSWORD")
    db_pool_size: int = _env("DB_POOL_SIZE", gt=0)
    db_statement_timeout_ms: int = _env("DB_STATEMENT_TIMEOUT_MS", gt=0)
    db_connect_timeout_s: float = _env("DB_CONNECT_TIMEOUT_S", default=10.0, gt=0)

    # HTTP server (the `api` entrypoint); PORT is the Cloud Run convention
    port: int = _env("PORT", default=8000, gt=0, le=65535)

    # LLM (model ids are checked against LIFECYCLE_TABLE above by src/model_lifecycle.py)
    anthropic_api_key: SecretStr = _env("ANTHROPIC_API_KEY")
    anthropic_base_url: str = _env("ANTHROPIC_BASE_URL", default="https://api.anthropic.com")
    llm_model_answer: str = _env("LLM_MODEL_ANSWER", default="claude-sonnet-5-5")
    llm_model_aux: str = _env("LLM_MODEL_AUX", default="claude-haiku-5-5")
    llm_model_judge: str = _env("LLM_MODEL_JUDGE", default="claude-haiku-5-5")
    llm_model_fallback: str = _env("LLM_MODEL_FALLBACK", default="claude-sonnet-4-6")
    llm_fallback_enabled: bool = _env("LLM_FALLBACK_ENABLED", default=False)
    llm_max_iterations: int = _env("LLM_MAX_ITERATIONS", default=3, gt=0)
    # A model retiring within this many days is reported (arch 06 section 6.6).
    model_retire_warn_days: int = _env("MODEL_RETIRE_WARN_DAYS", default=30, ge=0)

    # Embeddings
    voyage_api_key: SecretStr = _env("VOYAGE_API_KEY")

    # Langfuse: host and both keys are required only when enabled
    langfuse_enabled: bool = _env("LANGFUSE_ENABLED", default=False)
    langfuse_host: str | None = _env("LANGFUSE_HOST", default=None)
    langfuse_public_key: str | None = _env("LANGFUSE_PUBLIC_KEY", default=None)
    langfuse_secret_key: SecretStr | None = _env("LANGFUSE_SECRET_KEY", default=None)

    # Seconds /readyz waits for its database check (SELECT 1) before reporting it failing.
    readiness_timeout_s: float = _env("READINESS_TIMEOUT_S", default=2.0, gt=0)
    # Seconds /readyz serves its last result (also a failing one) before probing again.
    readiness_cache_ttl_s: float = _env("READINESS_CACHE_TTL_S", default=5.0, gt=0)

    # Source connectors: max requests per second to the FCA Handbook API. No vendor limit is
    # published (docs/spikes/fca-handbook-api.md), so this is a conservative figure. The mode, key
    # and the other FCA knobs belong to the connector (E11-T20), not to app boot.
    fca_api_rate_limit_rps: float = _env("FCA_API_RATE_LIMIT_RPS", default=1.0, gt=0)

    # PII
    pii_mode: str = _env("PII_MODE", default="regex")
    gcp_project_id: str = _env("GCP_PROJECT_ID", default="")

    # Auth (MVP; E1 replaces it)
    jwt_secret: SecretStr = _env("JWT_SECRET")

    @field_validator("db_host", "db_name", "db_user", mode="after")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator(
        "db_password", "anthropic_api_key", "voyage_api_key", "jwt_secret", mode="after"
    )
    @classmethod
    def _secret_not_blank(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("must not be empty")
        if secret.lower() in PLACEHOLDER_SECRETS:
            raise ValueError("is still a placeholder; set a real value")
        return value

    @field_validator("anthropic_base_url", mode="after")
    @classmethod
    def _base_url_safe(cls, value: str) -> str:
        # The API key is sent to this URL, so it must be https (http only for local servers)
        # and must not carry credentials. Messages never echo the value.
        try:
            parts = urlsplit(value)
            host = parts.hostname
        except ValueError:
            raise ValueError("is not a valid URL") from None
        if parts.scheme not in ("http", "https") or not host:
            raise ValueError("must be an absolute http(s) URL")
        if parts.username is not None or parts.password is not None:
            raise ValueError("must not contain credentials (userinfo)")
        if parts.scheme == "http" and host not in LOCAL_HTTP_HOSTS:
            raise ValueError("must use https (http is allowed only for localhost)")
        return value

    @field_validator("jwt_secret", mode="after")
    @classmethod
    def _jwt_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(f"must be at least {JWT_SECRET_MIN_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _check_cross_field_rules(self) -> Settings:
        if self.llm_model_judge == self.llm_model_answer:
            raise ValueError("LLM_MODEL_JUDGE must differ from LLM_MODEL_ANSWER")
        if self.langfuse_enabled:
            missing = [
                name
                for name, value in (
                    ("LANGFUSE_HOST", self.langfuse_host),
                    ("LANGFUSE_PUBLIC_KEY", self.langfuse_public_key),
                    ("LANGFUSE_SECRET_KEY", self.langfuse_secret_key),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"LANGFUSE_ENABLED=true requires {', '.join(missing)}")
        return self

    def database_dsn_for(self, database: str) -> str:
        """SQLAlchemy asyncpg DSN for `database` on the configured server. Contains the password."""
        user = quote(self.db_user, safe="")
        password = quote(self.db_password.get_secret_value(), safe="")
        return f"postgresql+asyncpg://{user}:{password}@{self.db_host}:{self.db_port}/{database}"

    @property
    def database_dsn(self) -> str:
        return self.database_dsn_for(self.db_name)
