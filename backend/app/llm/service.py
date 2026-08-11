import asyncio
import logging
from collections.abc import Awaitable, Callable

from backend.app.llm.errors import (
    ChatOutputError,
    ChatProviderRejectedError,
    ChatUnavailableError,
    ProviderConfigurationError,
    ProviderNonRetryableError,
    ProviderOutputError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatResult
from backend.app.llm.policy import StageOneResponsePolicy
from backend.app.llm.prompt_builder import PromptBuilder
from backend.app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


class ChatService:
    """Orchestrate prompt construction and bounded provider retries."""

    def __init__(
        self,
        provider: LLMProvider,
        prompt_builder: PromptBuilder,
        response_policy: StageOneResponsePolicy | None = None,
        *,
        max_retries: int,
        retry_delay_seconds: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._provider = provider
        self._prompt_builder = prompt_builder
        self._response_policy = response_policy or StageOneResponsePolicy()
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep

    async def chat(self, user_message: str) -> ChatResult:
        policy_response = self._response_policy.pre_provider_response(user_message)
        if policy_response is not None:
            return policy_response
        prompt = self._prompt_builder.build(user_message)
        for attempt in range(self._max_retries + 1):
            try:
                result = await self._provider.complete(prompt)
                return self._response_policy.post_provider_response(result)
            except ProviderOutputError as exc:
                raise ChatOutputError("provider returned invalid output") from exc
            except ProviderNonRetryableError as exc:
                raise ChatProviderRejectedError("provider rejected request") from exc
            except ProviderConfigurationError as exc:
                raise ChatUnavailableError("provider is not configured") from exc
            except (ProviderTimeoutError, ProviderRetryableError) as exc:
                if attempt == self._max_retries:
                    raise ChatUnavailableError("provider remained unavailable") from exc
                logger.warning(
                    "llm_retry attempt=%s error_type=%s",
                    attempt + 1,
                    type(exc).__name__,
                )
                await self._sleep(self._retry_delay_seconds * (attempt + 1))

        raise AssertionError("retry loop must return or raise")
