import json
from collections.abc import Mapping
from typing import Any, Protocol, cast

import httpx

from backend.app.core.config import Settings
from backend.app.llm.errors import (
    ProviderConfigurationError,
    ProviderNonRetryableError,
    ProviderOutputError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage


class LLMProvider(Protocol):
    """Port implemented by any provider adapter used by the chat service."""

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        """Return a normalized completion or raise a typed provider error."""


class OpenAICompatibleProvider:
    """Small OpenAI-compatible chat-completions adapter with no SDK coupling."""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ):
        self._enabled = settings.llm_enabled
        self._api_key = settings.llm_api_key
        self._base_url = settings.llm_base_url
        self._model = settings.llm_model
        self._timeout_seconds = settings.llm_timeout_seconds
        self._transport = transport

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        if not self._enabled or self._api_key is None or self._model is None:
            raise ProviderConfigurationError("LLM provider is not configured")

        request_body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        timeout = httpx.Timeout(self._timeout_seconds)
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=timeout,
                transport=self._transport,
            ) as client:
                response = await client.post("/chat/completions", json=request_body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.TransportError as exc:
            raise ProviderRetryableError("provider transport failed") from exc

        if response.status_code in {408, 429} or response.status_code >= 500:
            raise ProviderRetryableError("provider returned a temporary failure")
        if response.status_code >= 400:
            raise ProviderNonRetryableError("provider rejected the request")

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderOutputError("provider response was not JSON") from exc
        return self._map_response(payload)

    def _map_response(self, payload: Any) -> ChatResult:
        if not isinstance(payload, Mapping):
            raise ProviderOutputError("provider response must be an object")

        model = payload.get("model")
        choices = payload.get("choices")
        usage = payload.get("usage")
        if not isinstance(model, str) or not model.strip():
            raise ProviderOutputError("provider response omitted model")
        if not isinstance(choices, list) or not choices:
            raise ProviderOutputError("provider response omitted choices")
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise ProviderOutputError("provider choice was invalid")
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise ProviderOutputError("provider message was invalid")
        answer = message.get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise ProviderOutputError("provider response omitted text content")
        if not isinstance(usage, Mapping):
            raise ProviderOutputError("provider response omitted usage")

        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (input_tokens, output_tokens, total_tokens)
        ):
            raise ProviderOutputError("provider usage was invalid")
        normalized_input_tokens = cast(int, input_tokens)
        normalized_output_tokens = cast(int, output_tokens)
        normalized_total_tokens = cast(int, total_tokens)
        if (
            normalized_total_tokens
            != normalized_input_tokens + normalized_output_tokens
        ):
            raise ProviderOutputError("provider usage total was inconsistent")

        return ChatResult(
            answer=answer.strip(),
            model=model,
            usage=TokenUsage(
                input_tokens=normalized_input_tokens,
                output_tokens=normalized_output_tokens,
                total_tokens=normalized_total_tokens,
            ),
        )
