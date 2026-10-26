"""Embedding provider boundary and a deterministic local-development provider."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from backend.app.embeddings.errors import EmbeddingConfigurationError
from backend.app.embeddings.models import EmbeddingBatch

_TOKEN_PATTERNS = {
    # Preserve the original v1 behavior so persisted vectors remain reproducible.
    "deterministic-hash-v1": re.compile(r"[\\w]+", re.UNICODE),
    # v2 corrects the word-character class; model ID scopes the new vectors.
    "deterministic-hash-v2": re.compile(r"[\w]+", re.UNICODE),
}


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A replaceable, batch-oriented embedding provider boundary."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class DeterministicHashEmbeddingProvider:
    """Local non-network provider for development and deterministic tests.

    This intentionally is not presented as a production-quality semantic model. It
    sends no content to a third party and uses signed feature hashing only to make
    the Stage 3 vector pipeline and ranking tests reproducible.
    """

    def __init__(self, *, model: str, dimensions: int) -> None:
        token_pattern = _TOKEN_PATTERNS.get(model)
        if token_pattern is None:
            raise EmbeddingConfigurationError
        self._model = model
        self._dimensions = dimensions
        self._token_pattern = token_pattern

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            model=self.model,
            dimensions=self.dimensions,
            vectors=tuple(self._embed_one(text) for text in texts),
        )

    def _embed_one(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimensions
        tokens = self._token_pattern.findall(text.casefold())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            values[bucket] += sign
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude == 0:
            # Non-empty normalized chunks may contain only punctuation. Hashing the
            # whole string keeps the vector valid without claiming semantic meaning.
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            values[int.from_bytes(digest[:4], "big") % self.dimensions] = 1.0
            magnitude = 1.0
        return tuple(value / magnitude for value in values)
