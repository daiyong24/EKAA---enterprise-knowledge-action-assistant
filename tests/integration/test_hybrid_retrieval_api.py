from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.embeddings.hybrid import HybridRetrievalService
from backend.app.embeddings.models import RetrievedChunk
from backend.app.main import create_app


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        document_id="public-document",
        document_name="public.txt",
        chunk_id="public-chunk",
        chunk_index=0,
        content="Synthetic public incident INC-1042 evidence.",
        score=0.8,
        page_number=None,
        section=None,
    )


class _Semantic:
    def search(self, **_: object) -> list[RetrievedChunk]:
        return [_chunk()]


class _Lexical:
    def search_with_metadata(self, **_: object) -> tuple[list[RetrievedChunk], int]:
        return [_chunk()], 1


@pytest.mark.anyio
async def test_hybrid_api_returns_opt_in_safe_count_diagnostics() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        embedding_enabled=True,
        hybrid_retrieval_enabled=True,
        _env_file=None,
    )
    app = create_app(settings)
    app.state.retrieval_service = HybridRetrievalService(
        semantic_service=_Semantic(),  # type: ignore[arg-type]
        lexical_service=_Lexical(),  # type: ignore[arg-type]
        semantic_candidate_count=1,
        lexical_candidate_count=1,
        rrf_rank_constant=60,
        max_top_k=20,
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/retrieval/search",
            json={"query": "private INC-1042 input", "include_diagnostics": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["diagnostics"] == {
        "semantic_candidate_count": 1,
        "lexical_candidate_count": 1,
        "merged_candidate_count": 1,
        "exact_identifier_candidate_count": 1,
    }
    assert "private INC-1042 input" not in body["diagnostics"].__repr__()
