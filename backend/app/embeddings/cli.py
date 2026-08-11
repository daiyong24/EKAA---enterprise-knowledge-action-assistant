"""Controlled non-public maintenance command for embedding completed chunks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import configure_logging
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.embeddings.provider import DeterministicHashEmbeddingProvider
from backend.app.embeddings.service import EmbeddingService


def run_embed_pending(settings: Settings, service: EmbeddingService) -> int:
    """Run one bounded maintenance pass only when pgvector was explicitly enabled."""
    if not settings.embedding_enabled:
        return 2
    result = service.embed_pending_chunks()
    print(json.dumps(result.__dict__, sort_keys=True))
    return 0 if result.failed_batch_count == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EKAA embedding maintenance")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("embed-pending", help="embed unembedded completed chunks")
    arguments = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    if arguments.command != "embed-pending":
        return 2
    engine = create_database_engine(settings.database_url)
    try:
        provider = DeterministicHashEmbeddingProvider(
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
        service = EmbeddingService(
            session_factory=create_session_factory(engine),
            provider=provider,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
        )
        return run_embed_pending(settings, service)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
