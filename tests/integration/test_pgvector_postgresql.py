"""Opt-in EKAA end-to-end PostgreSQL + pgvector verification."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from pgvector.psycopg import register_vector
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL, make_url

from alembic import command
from backend.app.db.models import ChunkEmbedding, Document, DocumentChunk
from backend.app.db.session import create_session_factory
from backend.app.embeddings.models import EmbeddingBatch, EmbeddingInput
from backend.app.embeddings.repository import EmbeddingRepository
from backend.app.embeddings.service import EmbeddingService, SemanticRetrievalService

TEST_POSTGRES_URL = os.getenv("EKAA_TEST_POSTGRES_URL")


class _FixtureProvider:
    model = "fixture-model"
    dimensions = 3

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors = {
            "alpha one": (1.0, 0.0, 0.0),
            "alpha two": (1.0, 0.0, 0.0),
            "beta": (0.0, 1.0, 0.0),
            "network query": (1.0, 0.0, 0.0),
        }
        return EmbeddingBatch(
            model=self.model,
            dimensions=self.dimensions,
            vectors=tuple(vectors[text] for text in texts),
        )


@pytest.fixture
def pgvector_engine() -> Iterator[Engine]:
    if not TEST_POSTGRES_URL:
        pytest.skip("requires explicit disposable EKAA_TEST_POSTGRES_URL with pgvector")
    admin_url = make_url(TEST_POSTGRES_URL)
    schema = f"ekaa_test_{uuid4().hex}"
    admin_engine = create_engine(admin_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url: URL = admin_url.update_query_dict(
        {"options": f"-csearch_path={schema},public"}
    )
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    # Alembic's ConfigParser treats percent signs as interpolation markers.  The
    # encoded ``options`` query parameter contains percent escapes, so double
    # them at the ConfigParser boundary; the resulting engine URL is unchanged.
    config.set_main_option(
        "sqlalchemy.url", scoped_url.render_as_string(False).replace("%", "%%")
    )
    had_runtime_database_url = "EKAA_DATABASE_URL" in os.environ
    runtime_database_url = os.environ.pop("EKAA_DATABASE_URL", None)
    engine: Engine | None = None
    try:
        # ``alembic/env.py`` intentionally honors EKAA_DATABASE_URL in normal
        # runtime.  This disposable test must instead migrate its scoped test URL.
        command.upgrade(config, "head")
        engine = create_engine(scoped_url)

        @event.listens_for(engine, "connect")
        def _register_vector(dbapi_connection: object, _: object) -> None:
            register_vector(dbapi_connection)  # type: ignore[arg-type]

        yield engine
    finally:
        if had_runtime_database_url:
            os.environ["EKAA_DATABASE_URL"] = runtime_database_url or ""
        if engine is not None:
            engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def test_ekaa_pgvector_migration_embedding_and_retrieval(
    pgvector_engine: Engine,
) -> None:
    session_factory = create_session_factory(pgvector_engine)
    with session_factory.begin() as session:
        document = Document(
            id="a" * 32,
            filename="synthetic.md",
            mime_type="text/markdown",
            checksum="d" * 64,
            status="completed",
            metadata_json={"format": "md"},
        )
        document.chunks = [
            DocumentChunk(
                id="a" * 31 + "1",
                chunk_index=0,
                content="alpha one",
                checksum="1" * 64,
                page_number=4,
                section="Operations",
                metadata_json={"source": "fixture"},
            ),
            DocumentChunk(
                id="a" * 31 + "2",
                chunk_index=1,
                content="alpha two",
                checksum="2" * 64,
                page_number=5,
                section="Operations",
                metadata_json={"source": "fixture"},
            ),
            DocumentChunk(
                id="a" * 31 + "3",
                chunk_index=2,
                content="beta",
                checksum="3" * 64,
                page_number=None,
                section="Other",
                metadata_json={"source": "fixture"},
            ),
        ]
        session.add(document)

    provider = _FixtureProvider()
    embedding_service = EmbeddingService(
        session_factory=session_factory,
        provider=provider,
        model=provider.model,
        dimensions=provider.dimensions,
        batch_size=3,
    )
    assert embedding_service.embed_pending_chunks().embedded_chunk_count == 3

    repository = EmbeddingRepository()
    with session_factory.begin() as session:
        inserted = repository.add_embeddings(
            session,
            inputs=[EmbeddingInput("a" * 31 + "1", "alpha one", "1" * 64)],
            vectors=[(1.0, 0.0, 0.0)],
            model=provider.model,
            dimensions=provider.dimensions,
        )
        session.add_all(
            [
                ChunkEmbedding(
                    chunk_id="a" * 31 + "1",
                    embedding_model="old-model",
                    embedding_dimensions=3,
                    embedding=[0.0, 1.0, 0.0],
                    content_checksum="1" * 64,
                ),
                ChunkEmbedding(
                    chunk_id="a" * 31 + "1",
                    embedding_model=provider.model,
                    embedding_dimensions=2,
                    embedding=[1.0, 0.0],
                    content_checksum="1" * 64,
                ),
            ]
        )
    assert inserted == 0

    retrieval_service = SemanticRetrievalService(
        session_factory=session_factory,
        provider=provider,
        model=provider.model,
        dimensions=provider.dimensions,
        max_top_k=3,
    )
    results = retrieval_service.search(query="network query", top_k=3)

    assert [result.chunk_id for result in results] == [
        "a" * 31 + "1",
        "a" * 31 + "2",
        "a" * 31 + "3",
    ]
    assert [result.score for result in results] == pytest.approx([1.0, 1.0, 0.0])
    assert results[0].document_id == "a" * 32
    assert results[0].document_name == "synthetic.md"
    assert results[0].page_number == 4
    assert results[0].section == "Operations"
