"""Expected RAG failures whose internal detail is never public."""


class RagError(Exception):
    """Base class for Stage 4 application-service failures."""


class RagUnavailableError(RagError):
    """The generation provider is unavailable after bounded attempts."""


class RagProviderRejectedError(RagError):
    """The generation provider rejected a valid EKAA request."""


class RagOutputError(RagError):
    """The generation provider returned an invalid normalized answer."""
