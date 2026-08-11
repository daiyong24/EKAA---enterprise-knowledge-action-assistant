from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EKAA_",
        extra="ignore",
    )

    app_name: str = "EKAA API"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_prefix: str = Field(default="", pattern=r"^$|^/.*[^/]$")
    llm_enabled: bool = False
    llm_provider: Literal["openai_compatible"] = "openai_compatible"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=3)
    llm_retry_delay_seconds: float = Field(default=0.1, ge=0, le=5)
    database_url: str = "sqlite:///./ekaa.db"
    ingestion_max_file_size_bytes: int = Field(
        default=10 * 1024 * 1024, gt=0, le=50 * 1024 * 1024
    )
    ingestion_max_request_size_bytes: int = Field(
        default=11 * 1024 * 1024, gt=0, le=55 * 1024 * 1024
    )
    ingestion_max_pdf_pages: int = Field(default=200, ge=1, le=1_000)
    ingestion_max_extracted_characters: int = Field(
        default=2_000_000, ge=1_000, le=10_000_000
    )
    ingestion_chunk_size_characters: int = Field(default=1_200, ge=100, le=10_000)
    ingestion_chunk_overlap_characters: int = Field(default=200, ge=0, le=2_000)

    @field_validator("llm_base_url")
    @classmethod
    def require_http_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("LLM base URL must use HTTP or HTTPS")
        return normalized

    @model_validator(mode="after")
    def reject_debug_in_production(self) -> Self:
        if self.environment == "production" and self.debug:
            raise ValueError("debug mode cannot be enabled in production")
        if self.environment == "production" and not self.llm_base_url.startswith(
            "https://"
        ):
            raise ValueError("production LLM base URL must use HTTPS")
        if self.llm_enabled and (self.llm_api_key is None or not self.llm_model):
            raise ValueError("enabled LLM requires both an API key and model")
        if (
            self.ingestion_chunk_overlap_characters
            >= self.ingestion_chunk_size_characters
        ):
            raise ValueError("ingestion chunk overlap must be smaller than chunk size")
        if self.ingestion_max_request_size_bytes < self.ingestion_max_file_size_bytes:
            raise ValueError("ingestion request limit must cover the file size limit")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
