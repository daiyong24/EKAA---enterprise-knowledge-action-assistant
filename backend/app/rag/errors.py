"""Expected RAG failures whose internal detail is never public."""

from backend.app.llm.models import TokenUsage


class RagError(Exception):
    """Base class for Stage 4 application-service failures."""


class RagUnavailableError(RagError):
    """The generation provider is unavailable after bounded attempts."""


class RagProviderRejectedError(RagError):
    """The generation provider rejected a valid EKAA request."""


class RagOutputError(RagError):
    """The generation provider returned an invalid normalized answer."""

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage
