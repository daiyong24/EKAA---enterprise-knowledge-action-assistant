from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
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

    @model_validator(mode="after")
    def reject_debug_in_production(self) -> Self:
        if self.environment == "production" and self.debug:
            raise ValueError("debug mode cannot be enabled in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
