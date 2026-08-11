import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


def test_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.api_prefix == ""


def test_api_prefix_rejects_trailing_slash() -> None:
    with pytest.raises(ValidationError):
        Settings(api_prefix="/api/", _env_file=None)


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", debug=True, _env_file=None)


def test_enabled_llm_requires_explicit_model_and_key() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_enabled=True, _env_file=None)


def test_production_rejects_insecure_llm_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            llm_base_url="http://localhost:1234",
            _env_file=None,
        )
