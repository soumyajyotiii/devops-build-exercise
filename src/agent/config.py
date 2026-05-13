"""Service configuration, populated from environment variables.

Reads all secrets and connection strings from the environment so the
container is the same across local / dev / staging / prod and the
infrastructure layer is responsible for wiring them up (Secrets Manager,
Parameter Store, etc.).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service
    environment: Literal["local", "dev", "staging", "prod"] = "local"
    log_level: str = "INFO"
    port: int = 8080

    # Data
    database_url: str = "postgresql+psycopg://saaf:saaf@localhost:5432/saaf"
    s3_bucket: str = "saaf-loan-docs-local"

    # LLM
    llm_provider: Literal["anthropic", "bedrock", "mock"] = "mock"
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str | None = None
    aws_region: str = "us-east-1"

    # Email
    ses_from_address: str = "loans@saaffinance.com"

    # Observability
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "underwriting-agent"

    @property
    def llm_is_mocked(self) -> bool:
        return self.llm_provider == "mock" or (
            self.llm_provider == "anthropic" and not self.anthropic_api_key
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
