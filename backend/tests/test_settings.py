from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import JWT_SECRET_MIN_LENGTH, PLACEHOLDER_SECRETS, Settings

BACKEND_DIR = Path(__file__).resolve().parent.parent

REQUIRED_KEYS = [
    "ANTHROPIC_API_KEY",
    "VOYAGE_API_KEY",
    "DB_PASSWORD",
    "JWT_SECRET",
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "DB_POOL_SIZE",
    "DB_STATEMENT_TIMEOUT_MS",
]

SECRET_KEYS = ["ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "DB_PASSWORD", "JWT_SECRET"]


def build() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestRequiredKeys:
    def test_valid_env_builds(self, settings_env: dict[str, str]) -> None:
        settings = build()
        assert settings.db_host == settings_env["DB_HOST"]
        assert settings.db_pool_size == 5
        assert settings.db_port == 5432
        assert settings.env_profile == "dev"

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_missing_key_names_it(self, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
        monkeypatch.delenv(key)
        with pytest.raises(ValidationError) as exc:
            build()
        assert key in str(exc.value)

    @pytest.mark.parametrize("key", SECRET_KEYS + ["DB_HOST", "DB_NAME", "DB_USER"])
    def test_empty_value_rejected(self, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
        monkeypatch.setenv(key, "  ")
        with pytest.raises(ValidationError) as exc:
            build()
        assert key in str(exc.value)

    @pytest.mark.parametrize("key", SECRET_KEYS)
    @pytest.mark.parametrize(
        "placeholder", ["REPLACE_ME", "changeme", "Change-Me", "PLACEHOLDER", "xxx", " XXX "]
    )
    def test_placeholder_secret_rejected(
        self, monkeypatch: pytest.MonkeyPatch, key: str, placeholder: str
    ) -> None:
        monkeypatch.setenv(key, placeholder)
        with pytest.raises(ValidationError) as exc:
            build()
        message = str(exc.value)
        assert key in message
        assert "placeholder" in message
        assert placeholder.strip() not in message

    def test_env_example_placeholder_rejected_for_every_secret(self) -> None:
        example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
        values = dict(
            line.split("=", 1) for line in example.splitlines() if "=" in line and line[0] != "#"
        )
        for key in SECRET_KEYS:
            assert values[key].strip().lower() in PLACEHOLDER_SECRETS

    def test_jwt_secret_too_short_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        short = "s" * (JWT_SECRET_MIN_LENGTH - 1)
        monkeypatch.setenv("JWT_SECRET", short)
        with pytest.raises(ValidationError) as exc:
            build()
        message = str(exc.value)
        assert "JWT_SECRET" in message
        assert str(JWT_SECRET_MIN_LENGTH) in message
        assert short not in message

    def test_jwt_secret_at_minimum_length_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "s" * JWT_SECRET_MIN_LENGTH)
        assert build().jwt_secret.get_secret_value() == "s" * JWT_SECRET_MIN_LENGTH

    def test_short_other_secrets_still_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_PASSWORD", "x")
        assert build().db_password.get_secret_value() == "x"

    @pytest.mark.parametrize("key", ["DB_POOL_SIZE", "DB_STATEMENT_TIMEOUT_MS"])
    def test_non_positive_int_rejected(self, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
        monkeypatch.setenv(key, "0")
        with pytest.raises(ValidationError) as exc:
            build()
        assert key in str(exc.value)

    def test_bad_profile_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV_PROFILE", "staging")
        with pytest.raises(ValidationError) as exc:
            build()
        assert "ENV_PROFILE" in str(exc.value)


class TestLangfuse:
    def test_enabled_without_keys_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        with pytest.raises(ValidationError) as exc:
            build()
        message = str(exc.value)
        for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            assert key in message

    def test_disabled_without_keys_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        assert build().langfuse_enabled is False

    def test_enabled_with_keys_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test.invalid")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "unit-lf-public")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "unit-lf-secret")
        assert build().langfuse_enabled is True


class TestModels:
    def test_defaults(self) -> None:
        settings = build()
        assert settings.llm_model_answer == "claude-sonnet-5-5"
        assert settings.llm_model_aux == "claude-haiku-5-5"
        assert settings.llm_model_judge == "claude-haiku-5-5"
        assert settings.llm_model_fallback == "claude-sonnet-4-6"
        assert settings.llm_fallback_enabled is False
        assert settings.llm_max_iterations == 3
        assert settings.anthropic_base_url == "https://api.anthropic.com"

    def test_judge_equal_to_answer_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_MODEL_JUDGE", "claude-sonnet-5-5")
        with pytest.raises(ValidationError) as exc:
            build()
        assert "LLM_MODEL_JUDGE" in str(exc.value)

    def test_validation_error_does_not_echo_secrets(
        self, monkeypatch: pytest.MonkeyPatch, settings_env: dict[str, str]
    ) -> None:
        monkeypatch.setenv("LLM_MODEL_JUDGE", "claude-sonnet-5-5")
        with pytest.raises(ValidationError) as exc:
            build()
        for key in SECRET_KEYS:
            assert settings_env[key] not in str(exc.value)


class TestEcfrRateLimit:
    def test_default_is_one_request_per_second_and_not_required(self) -> None:
        assert "CONNECTOR_RATE_RPS_ECFR" not in REQUIRED_KEYS
        assert build().ecfr_rate_limit_rps == 1.0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONNECTOR_RATE_RPS_ECFR", "0.5")
        assert build().ecfr_rate_limit_rps == 0.5

    @pytest.mark.parametrize("value", ["0", "-1", "abc"])
    def test_bad_value_rejected_naming_the_variable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("CONNECTOR_RATE_RPS_ECFR", value)
        with pytest.raises(ValidationError) as exc:
            build()
        assert "CONNECTOR_RATE_RPS_ECFR" in str(exc.value)

    def test_env_example_documents_the_default(self) -> None:
        example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
        assert "CONNECTOR_RATE_RPS_ECFR=1.0" in example


class TestFcaApiRateLimit:
    def test_default_is_one_request_per_second_and_not_required(self) -> None:
        assert "FCA_API_RATE_LIMIT_RPS" not in REQUIRED_KEYS
        assert build().fca_api_rate_limit_rps == 1.0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FCA_API_RATE_LIMIT_RPS", "0.5")
        assert build().fca_api_rate_limit_rps == 0.5

    @pytest.mark.parametrize("value", ["0", "-1", "abc"])
    def test_bad_value_rejected_naming_the_variable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("FCA_API_RATE_LIMIT_RPS", value)
        with pytest.raises(ValidationError) as exc:
            build()
        assert "FCA_API_RATE_LIMIT_RPS" in str(exc.value)

    def test_app_boot_does_not_depend_on_other_fca_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The spike's mode and key are not global config (E11-T20 owns them): even a bad value
        # for them must not stop the app from booting.
        monkeypatch.setenv("FCA_API_MODE", "live")
        monkeypatch.setenv("FCA_API_KEY", "demo-fca-key")
        build()
        fca_fields = [name for name in Settings.model_fields if name.startswith("fca_api_")]
        assert fca_fields == ["fca_api_rate_limit_rps"]


class TestAnthropicBaseUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://api.anthropic.com",
            "https://egress.example.com/proxy",
            "http://localhost",
            "http://localhost:8080",
            "http://127.0.0.1:9000",
            "http://[::1]:9000",
        ],
    )
    def test_accepted(self, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        assert build().anthropic_base_url == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "http://api.anthropic.com",
            "http://localhost.evil.com",
            "ftp://localhost",
            "api.anthropic.com",
            "https://",
            "https://user:s3cret-pw@api.anthropic.com",
            "https://user@api.anthropic.com",
            "http://user:s3cret-pw@localhost",
        ],
    )
    def test_rejected_names_var_and_hides_value(
        self, monkeypatch: pytest.MonkeyPatch, url: str
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        with pytest.raises(ValidationError) as exc:
            build()
        assert "ANTHROPIC_BASE_URL" in str(exc.value)
        assert "s3cret-pw" not in str(exc.value)


class TestDsn:
    def test_dsn_shape(self, settings_env: dict[str, str]) -> None:
        dsn = build().database_dsn
        assert dsn == (
            "postgresql+asyncpg://unit_user:unit-db-pass@db.test.invalid:5432/finadvisor_unit"
        )

    def test_dsn_url_encodes_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_PASSWORD", "p@ss:w/rd#1")
        monkeypatch.setenv("DB_USER", "us er")
        dsn = build().database_dsn
        assert "p%40ss%3Aw%2Frd%231" in dsn
        assert "us+er" in dsn or "us%20er" in dsn
        assert dsn.count("@") == 1

    def test_dsn_for_other_database(self) -> None:
        assert build().database_dsn_for("other_db").endswith("/other_db")


class TestNoLeaks:
    def test_repr_and_str_hide_secrets(self, settings_env: dict[str, str]) -> None:
        settings = build()
        for text in (repr(settings), str(settings)):
            for key in SECRET_KEYS:
                assert settings_env[key] not in text

    def test_langfuse_secret_hidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "unit-lf-secret")
        assert "unit-lf-secret" not in repr(build())


class TestBootFailure:
    def test_missing_anthropic_key_fails_boot(
        self, tmp_path: Path, settings_env: dict[str, str]
    ) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.update({k: v for k, v in settings_env.items() if k != "ANTHROPIC_API_KEY"})
        # cwd is an empty dir so no backend/.env can supply the key.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--app-dir",
                str(BACKEND_DIR),
                "--port",
                "0",
                "src.main:app",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0
        assert "ANTHROPIC_API_KEY" in result.stderr
        for key, value in settings_env.items():
            if key in SECRET_KEYS:
                assert value not in result.stderr


class TestEngine:
    def test_engine_uses_pool_size_and_statement_timeout(self) -> None:
        from src.db.session import build_engine

        factory = build_engine(build())
        engine = factory.kw["bind"]
        assert engine.pool.size() == 5  # type: ignore[attr-defined]
        assert engine.url.host == "db.test.invalid"
        assert "unit-db-pass" not in repr(engine.url)
