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

        request_body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        extension = self._request_body_extension(prompt)
        protected_fields = {"model", "messages"}
        if protected_fields.intersection(extension):
            raise ProviderConfigurationError(
                "provider request extension cannot replace EKAA prompt fields"
            )
        request_body.update(extension)
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        timeout = httpx.Timeout(self._timeout_seconds)
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=timeout,
                transport=self._transport,
                **self._client_options(),
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

    def _request_body_extension(self, prompt: ChatPrompt) -> Mapping[str, Any]:
        """Return provider-specific additions without changing the default body.

        This protected hook is deliberately internal: specialized local adapters may
        append controls, but may not replace the configured model or EKAA's ordered
        system/user prompt messages.
        """

        del prompt
        return {}

    def _client_options(self) -> Mapping[str, Any]:
        """Return internal HTTP-client options for specialized adapters only.

        The generic provider intentionally leaves HTTPX environment behavior at its
        existing default. A private local adapter may opt out when its egress
        boundary requires proxy isolation.
        """

        return {}

    def _map_response(self, payload: Any) -> ChatResult:
        if not isinstance(payload, Mapping):
            raise ProviderOutputError("provider response must be an object")

        usage = self._map_usage(payload.get("usage"))
        model = payload.get("model")
        choices = payload.get("choices")
        if not isinstance(model, str) or not model.strip():
            raise ProviderOutputError("provider response omitted model", usage=usage)
        if not isinstance(choices, list) or not choices:
            raise ProviderOutputError("provider response omitted choices", usage=usage)
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise ProviderOutputError("provider choice was invalid", usage=usage)
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise ProviderOutputError("provider message was invalid", usage=usage)
        answer = message.get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise ProviderOutputError(
                "provider response omitted text content", usage=usage
            )
        finish_reason = first_choice.get("finish_reason")
        if finish_reason is not None and (
            not isinstance(finish_reason, str) or not finish_reason.strip()
        ):
            raise ProviderOutputError("provider finish reason was invalid", usage=usage)

        return ChatResult(
            answer=answer.strip(),
            model=model,
            usage=usage,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _map_usage(usage: Any) -> TokenUsage:
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

        return TokenUsage(
            input_tokens=normalized_input_tokens,
            output_tokens=normalized_output_tokens,
            total_tokens=normalized_total_tokens,
        )
