"""Expected Stage 3 failures that never include chunk or provider payload content."""


class EmbeddingError(Exception):
    """Base failure for embedding generation or storage."""


class EmbeddingConfigurationError(EmbeddingError):
    """Configured model/dimension and provider metadata do not agree."""


class EmbeddingProviderError(EmbeddingError):
    """The provider could not supply a complete valid embedding batch."""


class EmbeddingStorageError(EmbeddingError):
    """A vector batch could not be committed consistently."""
