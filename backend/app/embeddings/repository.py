"""PostgreSQL/pgvector persistence and similarity-query boundary."""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import Select, case, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from backend.app.db.models import ChunkEmbedding, Document, DocumentChunk
from backend.app.embeddings.models import EmbeddingInput, RetrievedChunk

_IDENTIFIER_LEFT_BOUNDARY = r"(^|[^[:alnum:]_-])"
_IDENTIFIER_RIGHT_BOUNDARY = r"($|[^[:alnum:]_-])"


def exact_identifier_pattern(value: str) -> str:
    """Create a literal PostgreSQL regex with technical-ID boundaries.

    Identifier tokens are untrusted input. The boundaries prevent substring hits:
    ``INC-1042`` does not match ``XINC-1042`` or ``INC-10420``. Hyphen and
    underscore are intentionally identifier characters, while punctuation and
    whitespace delimit IDs. ``re.escape`` keeps any future token expansion literal.
    """
    escaped_value = re.escape(value.lower())
    return f"{_IDENTIFIER_LEFT_BOUNDARY}{escaped_value}{_IDENTIFIER_RIGHT_BOUNDARY}"


class EmbeddingRepository:
    """Database queries shaped for a future mandatory authorization filter."""

    def list_pending_chunks(
        self,
        session: Session,
        *,
        model: str,
        dimensions: int,
        limit: int,
    ) -> list[EmbeddingInput]:
        existing = (
            select(ChunkEmbedding.chunk_id)
            .where(ChunkEmbedding.embedding_model == model)
            .where(ChunkEmbedding.embedding_dimensions == dimensions)
        )
        statement = (
            select(DocumentChunk.id, DocumentChunk.content, DocumentChunk.checksum)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.status == "completed")
            .where(~DocumentChunk.id.in_(existing))
            .order_by(DocumentChunk.id.asc())
            .limit(limit)
        )
        return [
            EmbeddingInput(
                chunk_id=row.id, content=row.content, content_checksum=row.checksum
            )
            for row in session.execute(statement)
        ]

    def count_pending_chunks(
        self, session: Session, *, model: str, dimensions: int
    ) -> int:
        existing = (
            select(ChunkEmbedding.chunk_id)
            .where(ChunkEmbedding.embedding_model == model)
            .where(ChunkEmbedding.embedding_dimensions == dimensions)
        )
        statement = (
            select(func.count())
            .select_from(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.status == "completed")
            .where(~DocumentChunk.id.in_(existing))
        )
        return int(session.scalar(statement) or 0)

    def add_embeddings(
        self,
        session: Session,
        *,
        inputs: Sequence[EmbeddingInput],
        vectors: Sequence[tuple[float, ...]],
        model: str,
        dimensions: int,
    ) -> int:
        rows = [
            {
                "chunk_id": item.chunk_id,
                "embedding_model": model,
                "embedding_dimensions": dimensions,
                "embedding": list(vector),
                "content_checksum": item.content_checksum,
            }
            for item, vector in zip(inputs, vectors, strict=True)
        ]
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            statement = (
                postgresql_insert(ChunkEmbedding)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_chunk_embedding_model_dimension")
                .returning(ChunkEmbedding.id)
            )
            return len(list(session.scalars(statement)))
        session.add_all([ChunkEmbedding(**row) for row in rows])
        return len(rows)

    def search_statement(
        self,
        *,
        query_vector: Sequence[float],
        model: str,
        dimensions: int,
        top_k: int,
        allowed_document_ids: Sequence[str] | None,
    ) -> Select[tuple[DocumentChunk, Document, float]]:
        distance = ChunkEmbedding.embedding.cosine_distance(list(query_vector)).label(
            "distance"
        )
        statement = (
            select(DocumentChunk, Document, distance)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.status == "completed")
            .where(ChunkEmbedding.embedding_model == model)
            .where(ChunkEmbedding.embedding_dimensions == dimensions)
            .order_by(distance.asc(), DocumentChunk.id.asc())
            .limit(top_k)
        )
        # Stage 9 must provide the authorized IDs before this query executes. The
        # optional shape exists now so filters do not need a later query rewrite.
        if allowed_document_ids is not None:
            statement = statement.where(Document.id.in_(allowed_document_ids))
        return statement

    def search(
        self,
        session: Session,
        *,
        query_vector: Sequence[float],
        model: str,
        dimensions: int,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        statement = self.search_statement(
            query_vector=query_vector,
            model=model,
            dimensions=dimensions,
            top_k=top_k,
            allowed_document_ids=allowed_document_ids,
        )
        results: list[RetrievedChunk] = []
        for chunk, document, distance in session.execute(statement):
            results.append(
                RetrievedChunk(
                    document_id=document.id,
                    document_name=document.filename,
                    chunk_id=chunk.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=float(1.0 - distance),
                    page_number=chunk.page_number,
                    section=chunk.section,
                )
            )
        return results


class LexicalRetrievalRepository:
    """PostgreSQL full-text query boundary for Stage 6.

    The matching GIN expression index is installed by the Stage 6 migration.  The
    optional document-ID predicate deliberately mirrors vector retrieval so Stage 9
    can enforce authorization before either branch returns a chunk.
    """

    def search_statement(
        self,
        *,
        query: str,
        exact_identifiers: Sequence[str],
        top_k: int,
        allowed_document_ids: Sequence[str] | None,
    ) -> Select[tuple[DocumentChunk, Document, float, bool]]:
        vector = func.to_tsvector("simple", DocumentChunk.content)
        ts_query = func.websearch_to_tsquery("simple", query)
        full_text_match = vector.op("@@")(ts_query)
        exact_match = (
            or_(
                *[
                    func.lower(DocumentChunk.content).op("~")(
                        exact_identifier_pattern(identifier)
                    )
                    for identifier in exact_identifiers
                ]
            )
            if exact_identifiers
            else false()
        )
        # Exact identifiers (for example INC-1042) are literal, bounded candidates
        # even when FTS tokenization would split punctuation. They rank ahead of
        # generic lexical matches, but remain candidates for later RRF fusion.
        score = case(
            (exact_match, 1.0),
            else_=func.ts_rank_cd(vector, ts_query),
        ).label("lexical_score")
        statement = (
            select(DocumentChunk, Document, score, exact_match.label("exact_match"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.status == "completed")
            .where(or_(full_text_match, exact_match))
            .order_by(score.desc(), DocumentChunk.id.asc())
            .limit(top_k)
        )
        if allowed_document_ids is not None:
            statement = statement.where(Document.id.in_(allowed_document_ids))
        return statement

    def search(
        self,
        session: Session,
        *,
        query: str,
        exact_identifiers: Sequence[str],
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> tuple[list[RetrievedChunk], int]:
        statement = self.search_statement(
            query=query,
            exact_identifiers=exact_identifiers,
            top_k=top_k,
            allowed_document_ids=allowed_document_ids,
        )
        results: list[RetrievedChunk] = []
        exact_count = 0
        for chunk, document, score, exact_match in session.execute(statement):
            if exact_match:
                exact_count += 1
            results.append(
                RetrievedChunk(
                    document_id=document.id,
                    document_name=document.filename,
                    chunk_id=chunk.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=float(score),
                    page_number=chunk.page_number,
                    section=chunk.section,
                )
            )
        return results, exact_count
