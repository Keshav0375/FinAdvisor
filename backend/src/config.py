from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM Providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    litellm_master_key: str = "sk-litellm-local"

    # Embeddings
    voyage_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://finadvisor:localdev@localhost:5432/finadvisor"

    # PII
    pii_mode: str = "regex"
    gcp_project_id: str = ""

    # Kong
    kong_url: str = "http://localhost:8001"

    # Direct LLM base URL (bypasses Kong when set)
    llm_base_url: str = ""

    # LangFuse
    langfuse_host: str = "http://localhost:3030"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Auth
    jwt_secret: str = "local-dev-secret"
