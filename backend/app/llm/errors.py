from backend.app.llm.models import TokenUsage


class ProviderError(Exception):
    """Base class for provider-boundary failures; messages are never public."""


class ProviderConfigurationError(ProviderError):
    """The provider cannot be called with the current safe configuration."""


class ProviderTimeoutError(ProviderError):
    """The provider did not respond before the configured timeout."""


class ProviderRetryableError(ProviderError):
    """A temporary provider or transport failure that may be retried."""


class ProviderRateLimitError(ProviderRetryableError):
    """The provider asked the caller to slow down."""

    def __init__(
        self,
        message: str = "provider rate limit exceeded",
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderNonRetryableError(ProviderError):
    """A permanent provider request failure."""


class ProviderOutputError(ProviderError):
    """The provider response cannot satisfy the normalized output contract."""

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class ChatServiceError(Exception):
    """Base class for failures the API maps to a safe public error."""


class ChatUnavailableError(ChatServiceError):
    """The configured provider is unavailable after bounded attempts."""


class ChatProviderRejectedError(ChatServiceError):
    """The provider rejected a request which is valid at the EKAA boundary."""


class ChatOutputError(ChatServiceError):
    """The provider returned an invalid or incomplete response."""
