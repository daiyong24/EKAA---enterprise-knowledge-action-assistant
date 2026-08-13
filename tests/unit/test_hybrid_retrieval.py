from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy.dialects import postgresql

from backend.app.embeddings.hybrid import HybridRetrievalService
from backend.app.embeddings.models import RetrievedChunk
from backend.app.embeddings.repository import (
    LexicalRetrievalRepository,
    exact_identifier_pattern,
)
from backend.app.embeddings.service import (
    LexicalRetrievalService,
    extract_exact_identifiers,
)


def _chunk(chunk_id: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=f"document-{chunk_id}",
        document_name=f"{chunk_id}.txt",
        chunk_id=chunk_id,
        chunk_index=0,
        content=f"Synthetic public evidence for {chunk_id}.",
        score=score,
        page_number=None,
        section=None,
    )


class _Semantic:
    def __init__(self, results: Sequence[RetrievedChunk]) -> None:
        self.results = list(results)
        self.allowed_document_ids: Sequence[str] | None = None

    def search(
        self, *, allowed_document_ids: Sequence[str] | None = None, **_: object
    ) -> list[RetrievedChunk]:
        self.allowed_document_ids = allowed_document_ids
        return self.results


class _Lexical:
    def __init__(self, results: Sequence[RetrievedChunk], exact_count: int = 0) -> None:
        self.results = list(results)
        self.exact_count = exact_count
        self.allowed_document_ids: Sequence[str] | None = None

    def search_with_metadata(
        self, *, allowed_document_ids: Sequence[str] | None = None, **_: object
    ) -> tuple[list[RetrievedChunk], int]:
        self.allowed_document_ids = allowed_document_ids
        return self.results, self.exact_count


def _hybrid(
    semantic: Sequence[RetrievedChunk],
    lexical: Sequence[RetrievedChunk],
    *,
    exact_count: int = 0,
    rrf_rank_constant: int = 10,
) -> tuple[HybridRetrievalService, _Semantic, _Lexical]:
    semantic_service = _Semantic(semantic)
    lexical_service = _Lexical(lexical, exact_count)
    return (
        HybridRetrievalService(
            semantic_service=semantic_service,  # type: ignore[arg-type]
            lexical_service=lexical_service,  # type: ignore[arg-type]
            semantic_candidate_count=5,
            lexical_candidate_count=5,
            rrf_rank_constant=rrf_rank_constant,
            max_top_k=5,
        ),
        semantic_service,
        lexical_service,
    )


def test_hybrid_matches_semantic_on_natural_language_and_improves_mixed_fixture() -> (
    None
):
    # The semantic branch finds the natural-language runbook answer.  The lexical
    # branch adds the literal incident identifier; the shared answer wins under RRF.
    hybrid, _, _ = _hybrid(
        [_chunk("runbook", 0.9), _chunk("noise", 0.8)],
        [_chunk("incident-inc-1042", 1.0), _chunk("runbook", 0.6)],
        exact_count=1,
    )

    result = hybrid.search_with_diagnostics(
        query="How do I recover incident INC-1042?", top_k=2
    )

    assert [item.chunk_id for item in result.sources] == [
        "runbook",
        "incident-inc-1042",
    ]
    assert result.diagnostics.exact_identifier_candidate_count == 1
    # The declared mixed-query fixture's semantic-only first result is preserved
    # while its exact-ID evidence becomes available in the final Top-K.
    assert result.sources[0].chunk_id == "runbook"


def test_hybrid_merges_duplicates_and_uses_deterministic_chunk_id_ties() -> None:
    hybrid, _, _ = _hybrid(
        [_chunk("chunk-b"), _chunk("chunk-a")],
        [_chunk("chunk-a"), _chunk("chunk-b")],
        rrf_rank_constant=1,
    )

    result = hybrid.search_with_diagnostics(query="status", top_k=5)

    assert [item.chunk_id for item in result.sources] == ["chunk-a", "chunk-b"]
    assert result.diagnostics.merged_candidate_count == 2
    assert result.sources[0].score == pytest.approx(result.sources[1].score)
    assert result.sources[0].fusion_score == pytest.approx(
        result.sources[1].fusion_score
    )


def test_hybrid_handles_each_empty_branch_and_forwards_future_scope_filter() -> None:
    hybrid, semantic, lexical = _hybrid([], [_chunk("lexical-only")])
    result = hybrid.search_with_diagnostics(
        query="error-code-42", top_k=1, allowed_document_ids=["public-document"]
    )

    assert [item.chunk_id for item in result.sources] == ["lexical-only"]
    assert result.diagnostics.semantic_candidate_count == 0
    assert semantic.allowed_document_ids == ["public-document"]
    assert lexical.allowed_document_ids == ["public-document"]

    hybrid, _, _ = _hybrid([_chunk("semantic-only")], [])
    assert [item.chunk_id for item in hybrid.search(query="question", top_k=1)] == [
        "semantic-only"
    ]


def test_hybrid_rejects_out_of_range_top_k() -> None:
    hybrid, _, _ = _hybrid([], [])

    with pytest.raises(ValueError):
        hybrid.search(query="question", top_k=6)


def test_identifier_extraction_is_bounded_and_normalized() -> None:
    assert extract_exact_identifiers("Look up INC-1042 and KB_7 and 2024") == (
        "inc-1042",
        "kb_7",
        "2024",
    )
    assert len(extract_exact_identifiers("A-1 B-2 C-3 D-4 E-5 F-6")) == 5


def test_lexical_postgresql_statement_uses_fts_exact_ids_and_scope_filter() -> None:
    statement = LexicalRetrievalRepository().search_statement(
        query="recover incident INC-1042",
        exact_identifiers=("inc-1042",),
        top_k=5,
        allowed_document_ids=["public-document"],
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "to_tsvector" in sql
    assert "websearch_to_tsquery" in sql
    assert "@@" in sql
    assert "lower(document_chunks.content) ~" in sql
    assert "documents.id IN" in sql


def test_exact_identifier_regex_is_literal_and_has_technical_id_boundaries() -> None:
    statement = LexicalRetrievalRepository().search_statement(
        query="KB_7",
        exact_identifiers=("KB_7",),
        top_k=5,
        allowed_document_ids=None,
    )
    compiled = statement.compile(dialect=postgresql.dialect())

    assert exact_identifier_pattern("KB_7") == (
        r"(^|[^[:alnum:]_-])kb_7($|[^[:alnum:]_-])"
    )
    assert " ~ " in str(compiled)
    assert exact_identifier_pattern("KB_7") in compiled.params.values()
    assert exact_identifier_pattern("INC-1042") == (
        r"(^|[^[:alnum:]_-])inc\-1042($|[^[:alnum:]_-])"
    )


def test_postgresql_benchmark_explain_statement_compiles_without_literals() -> None:
    statement = LexicalRetrievalRepository().search_statement(
        query="benchmarkliteral",
        exact_identifiers=(),
        top_k=5,
        allowed_document_ids=["public-document"],
    )

    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    explain_sql = f"EXPLAIN (ANALYZE, COSTS OFF, BUFFERS) {compiled}"

    assert "to_tsvector" in explain_sql
    assert "websearch_to_tsquery" in explain_sql
    assert "__[POSTCOMPILE" not in explain_sql
    assert compiled.params


def test_lexical_service_returns_sorted_results_and_exact_metadata() -> None:
    class Session:
        def close(self) -> None:
            return None

    class Repository:
        def search(self, *_: object, **__: object) -> tuple[list[RetrievedChunk], int]:
            return [_chunk("chunk-z", 0.2), _chunk("chunk-a", 0.9)], 1

    service = LexicalRetrievalService(
        session_factory=Session,  # type: ignore[arg-type]
        max_top_k=3,
        repository=Repository(),  # type: ignore[arg-type]
    )

    results, exact_count = service.search_with_metadata(
        query="INC-1042", top_k=2, allowed_document_ids=["public-document"]
    )

    assert [item.chunk_id for item in results] == ["chunk-a", "chunk-z"]
    assert exact_count == 1
    assert service.search(query="status", top_k=1)[0].chunk_id == "chunk-a"
    with pytest.raises(ValueError):
        service.search(query="status", top_k=4)
