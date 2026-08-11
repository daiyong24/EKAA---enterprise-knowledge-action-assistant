class ProviderError(Exception):
    """Base class for provider-boundary failures; messages are never public."""


class ProviderConfigurationError(ProviderError):
    """The provider cannot be called with the current safe configuration."""


class ProviderTimeoutError(ProviderError):
    """The provider did not respond before the configured timeout."""


class ProviderRetryableError(ProviderError):
    """A temporary provider or transport failure that may be retried."""


class ProviderNonRetryableError(ProviderError):
    """A permanent provider request failure."""


class ProviderOutputError(ProviderError):
    """The provider response cannot satisfy the normalized output contract."""


class ChatServiceError(Exception):
    """Base class for failures the API maps to a safe public error."""


class ChatUnavailableError(ChatServiceError):
    """The configured provider is unavailable after bounded attempts."""


class ChatProviderRejectedError(ChatServiceError):
    """The provider rejected a request which is valid at the EKAA boundary."""


class ChatOutputError(ChatServiceError):
    """The provider returned an invalid or incomplete response."""
