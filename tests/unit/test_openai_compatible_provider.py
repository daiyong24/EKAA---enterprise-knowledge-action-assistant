import json

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.llm.errors import (
    ProviderOutputError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt
from backend.app.llm.provider import OpenAICompatibleProvider


def _settings() -> Settings:
    return Settings(
        environment="test",
        llm_enabled=True,
        llm_api_key="not-a-real-secret",
        llm_model="configured-test-model",
        _env_file=None,
    )


@pytest.mark.anyio
async def test_openai_compatible_provider_maps_usage_without_live_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer not-a-real-secret"
        assert json.loads(request.content) == {
            "model": "configured-test-model",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ],
        }
        return httpx.Response(
            200,
            json={
                "model": "provider-model",
                "choices": [{"message": {"content": "  Hello.  "}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 4,
                    "total_tokens": 11,
                },
            },
        )

    provider = OpenAICompatibleProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )

    result = await provider.complete(ChatPrompt(system="system", user="hello"))

    assert result.answer == "Hello."
    assert result.model == "provider-model"
    assert result.usage.model_dump() == {
        "input_tokens": 7,
        "output_tokens": 4,
        "total_tokens": 11,
    }
    assert result.finish_reason is None


def test_openai_compatible_provider_rejects_invalid_usage() -> None:
    provider = OpenAICompatibleProvider(_settings())

    with pytest.raises(ProviderOutputError):
        provider._map_response(
            {
                "model": "provider-model",
                "choices": [{"message": {"content": "Hello."}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 99,
                },
            }
        )


def test_openai_compatible_provider_retains_valid_usage_on_late_output_error() -> None:
    provider = OpenAICompatibleProvider(_settings())

    with pytest.raises(ProviderOutputError) as error:
        provider._map_response(
            {
                "model": "provider-model",
                "choices": [{"message": {"content": ""}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            }
        )

    assert error.value.usage is not None
    assert error.value.usage.model_dump() == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
    }


@pytest.mark.anyio
async def test_openai_compatible_provider_marks_rate_limit_retryable() -> None:
    provider = OpenAICompatibleProvider(
        _settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(429)),
    )

    with pytest.raises(ProviderRetryableError):
        await provider.complete(ChatPrompt(system="system", user="hello"))


@pytest.mark.anyio
async def test_openai_compatible_provider_maps_read_timeout_to_timeout_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("local provider did not respond")

    provider = OpenAICompatibleProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(ProviderTimeoutError):
        await provider.complete(ChatPrompt(system="system", user="hello"))
