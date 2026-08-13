"""Opt-in EKAA end-to-end PostgreSQL + pgvector verification."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

import pytest
from alembic.config import Config
from pgvector.psycopg import register_vector
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import URL, make_url

from alembic import command
from backend.app.db.models import ChunkEmbedding, Document, DocumentChunk
from backend.app.db.session import create_session_factory
from backend.app.embeddings.hybrid import HybridRetrievalService
from backend.app.embeddings.models import EmbeddingBatch, EmbeddingInput
from backend.app.embeddings.repository import (
    EmbeddingRepository,
    LexicalRetrievalRepository,
)
from backend.app.embeddings.service import (
    EmbeddingService,
    LexicalRetrievalService,
    SemanticRetrievalService,
)

TEST_POSTGRES_URL = os.getenv("EKAA_TEST_POSTGRES_URL")
_BENCHMARK_CHUNK_COUNT = 512
_BENCHMARK_WARMUPS = 3
_BENCHMARK_REPETITIONS = 9
# Non-production local regression guard, deliberately loose for hardware variance.
_BENCHMARK_MAX_HYBRID_MEDIAN_SECONDS = 0.5
_BENCHMARK_MAX_HYBRID_TO_SEMANTIC_RATIO = 5.0


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


class _BenchmarkProvider:
    model = "benchmark-model"
    dimensions = 3

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            model=self.model,
            dimensions=self.dimensions,
            vectors=tuple((1.0, 0.0, 0.0) for _ in texts),
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


def test_hybrid_lexical_index_plan_and_identifier_candidate_at_demo_scale(
    pgvector_engine: Engine,
) -> None:
    """Verify the Stage 6 PostgreSQL plan with 512 synthetic/public chunks.

    This is intentionally opt-in with the shared disposable pgvector fixture.  It
    records a real planner result rather than pretending SQLite's relational backend
    is full-text search.
    """
    session_factory = create_session_factory(pgvector_engine)
    with session_factory.begin() as session:
        document = Document(
            id="b" * 32,
            filename="hybrid-demo.txt",
            mime_type="text/plain",
            checksum="e" * 64,
            status="completed",
            metadata_json={"format": "txt"},
        )
        document.chunks = [
            DocumentChunk(
                id=f"{index:032x}",
                chunk_index=index,
                content=(
                    "Synthetic public recovery guidance for INC-1042."
                    if index == 7
                    else f"Synthetic public operational document {index}."
                ),
                checksum=f"{index + 1:064x}",
                page_number=None,
                section="Demo",
                metadata_json={"source": "stage-6-fixture"},
            )
            for index in range(512)
        ]
        session.add(document)

    with pgvector_engine.begin() as connection:
        connection.execute(text("ANALYZE document_chunks"))
        plan = "\n".join(
            row[0]
            for row in connection.execute(
                text(
                    "EXPLAIN (COSTS OFF) SELECT id FROM document_chunks "
                    "WHERE to_tsvector('simple', content) "
                    "@@ websearch_to_tsquery('simple', 'INC-1042')"
                )
            )
        )
    assert "ix_document_chunks_content_fts_simple" in plan

    repository = LexicalRetrievalRepository()
    with session_factory() as session:
        results, exact_count = repository.search(
            session,
            query="INC-1042",
            exact_identifiers=("inc-1042",),
            top_k=5,
            allowed_document_ids=["b" * 32],
        )

    assert [result.chunk_id for result in results] == [f"{7:032x}"]
    assert exact_count == 1


def test_hybrid_identifier_underscore_is_literal_in_postgresql(
    pgvector_engine: Engine,
) -> None:
    """`KB_7` must be literal and cannot be an ID substring."""
    session_factory = create_session_factory(pgvector_engine)
    with session_factory.begin() as session:
        document = Document(
            id="c" * 32,
            filename="identifier-demo.txt",
            mime_type="text/plain",
            checksum="f" * 64,
            status="completed",
            metadata_json={"format": "txt"},
        )
        document.chunks = [
            DocumentChunk(
                id="c" * 31 + "1",
                chunk_index=0,
                content="Synthetic reference KB_7 is the approved guide.",
                checksum="1" * 64,
                page_number=None,
                section="Demo",
                metadata_json={"source": "stage-6-fixture"},
            ),
            DocumentChunk(
                id="c" * 31 + "2",
                chunk_index=1,
                content="Synthetic reference KBX7 is a different guide.",
                checksum="2" * 64,
                page_number=None,
                section="Demo",
                metadata_json={"source": "stage-6-fixture"},
            ),
            DocumentChunk(
                id="c" * 31 + "3",
                chunk_index=2,
                content="Synthetic reference XKB_7 is a different guide.",
                checksum="3" * 64,
                page_number=None,
                section="Demo",
                metadata_json={"source": "stage-6-fixture"},
            ),
            DocumentChunk(
                id="c" * 31 + "4",
                chunk_index=3,
                content="Synthetic reference KB_7X is a different guide.",
                checksum="4" * 64,
                page_number=None,
                section="Demo",
                metadata_json={"source": "stage-6-fixture"},
            ),
        ]
        session.add(document)

    with session_factory() as session:
        results, exact_count = LexicalRetrievalRepository().search(
            session,
            query="unrelated-token",
            exact_identifiers=("kb_7",),
            top_k=5,
            allowed_document_ids=["c" * 32],
        )

    assert [result.chunk_id for result in results] == ["c" * 31 + "1"]
    assert exact_count == 1


def test_hybrid_retrieval_latency_guard_and_fts_plan_at_demo_scale(
    pgvector_engine: Engine,
) -> None:
    """Measure actual query paths on 512 synthetic chunks in a disposable schema.

    Run this opt-in test with ``-s`` to retain median timings in terminal logs. The
    guard is a local regression tripwire, not a production SLO or performance claim.
    """
    session_factory = create_session_factory(pgvector_engine)
    document_id = "d" * 32
    target_chunk_id = f"{7:032x}"
    with session_factory.begin() as session:
        document = Document(
            id=document_id,
            filename="hybrid-benchmark.txt",
            mime_type="text/plain",
            checksum="a" * 63 + "f",
            status="completed",
            metadata_json={"format": "txt"},
        )
        document.chunks = [
            DocumentChunk(
                id=f"{index:032x}",
                chunk_index=index,
                content=(
                    "Synthetic public synthetic benchmark question benchmarkliteral "
                    "recovery guidance."
                    if index == 7
                    else f"Synthetic public operational benchmark document {index}."
                ),
                checksum=f"{index + 1000:064x}",
                page_number=None,
                section="Benchmark",
                metadata_json={"source": "stage-6-benchmark"},
            )
            for index in range(_BENCHMARK_CHUNK_COUNT)
        ]
        session.add(document)

    provider = _BenchmarkProvider()
    with session_factory.begin() as session:
        session.add_all(
            [
                ChunkEmbedding(
                    chunk_id=f"{index:032x}",
                    embedding_model=provider.model,
                    embedding_dimensions=provider.dimensions,
                    embedding=([1.0, 0.0, 0.0] if index == 7 else [0.0, 1.0, 0.0]),
                    content_checksum=f"{index + 1000:064x}",
                )
                for index in range(_BENCHMARK_CHUNK_COUNT)
            ]
        )

    semantic = SemanticRetrievalService(
        session_factory=session_factory,
        provider=provider,
        model=provider.model,
        dimensions=provider.dimensions,
        max_top_k=10,
    )
    lexical = LexicalRetrievalService(session_factory=session_factory, max_top_k=10)
    hybrid = HybridRetrievalService(
        semantic_service=semantic,
        lexical_service=lexical,
        semantic_candidate_count=5,
        lexical_candidate_count=5,
        rrf_rank_constant=60,
        max_top_k=10,
    )
    mixed_query = "synthetic benchmark question benchmarkliteral"

    # Prove the actual FTS repository statement can use the GIN index at the
    # declared demo scale. No exact-ID predicate is used here because its bounded
    # regex branch is intentionally a separate literal-candidate behavior.
    statement = LexicalRetrievalRepository().search_statement(
        query="benchmarkliteral",
        exact_identifiers=(),
        top_k=5,
        allowed_document_ids=[document_id],
    )
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    with pgvector_engine.begin() as connection:
        connection.execute(text("ANALYZE document_chunks"))
        # At 512 rows PostgreSQL may correctly favour a sequential scan depending
        # on local statistics/cost settings. Disable only that alternative inside
        # this disposable transaction to verify the actual FTS predicate is index-
        # compatible; the separately timed service queries use normal planner state.
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, COSTS OFF, BUFFERS) {compiled}",
                compiled.params,
            )
        )
    assert "ix_document_chunks_content_fts_simple" in plan
    print(f"stage6_fts_plan\n{plan}")

    # Warm the same application services first; only actual repository/service
    # queries below are timed. Hybrid executes semantic plus lexical retrieval.
    for _ in range(_BENCHMARK_WARMUPS):
        semantic.search(query=mixed_query, top_k=5)
        hybrid.search(query=mixed_query, top_k=5)

    semantic_samples: list[float] = []
    hybrid_samples: list[float] = []
    for _ in range(_BENCHMARK_REPETITIONS):
        start = perf_counter()
        semantic_result = semantic.search(query=mixed_query, top_k=5)
        semantic_samples.append(perf_counter() - start)
        start = perf_counter()
        hybrid_result = hybrid.search(query=mixed_query, top_k=5)
        hybrid_samples.append(perf_counter() - start)

    semantic_median = median(semantic_samples)
    hybrid_median = median(hybrid_samples)
    allowed_hybrid_median = max(
        _BENCHMARK_MAX_HYBRID_MEDIAN_SECONDS,
        semantic_median * _BENCHMARK_MAX_HYBRID_TO_SEMANTIC_RATIO,
    )
    print(
        "stage6_hybrid_latency "
        f"chunks={_BENCHMARK_CHUNK_COUNT} warmups={_BENCHMARK_WARMUPS} "
        f"repetitions={_BENCHMARK_REPETITIONS} "
        f"semantic_median_seconds={semantic_median:.6f} "
        f"hybrid_median_seconds={hybrid_median:.6f} "
        f"allowed_hybrid_median_seconds={allowed_hybrid_median:.6f}"
    )
    assert semantic_result[0].chunk_id == target_chunk_id
    assert hybrid_result[0].chunk_id == target_chunk_id
    assert hybrid_median <= allowed_hybrid_median
