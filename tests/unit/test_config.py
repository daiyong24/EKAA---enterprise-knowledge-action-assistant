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
