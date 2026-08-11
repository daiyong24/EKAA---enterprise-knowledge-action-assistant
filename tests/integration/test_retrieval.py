from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.embeddings.errors import EmbeddingProviderError
from backend.app.embeddings.models import RetrievedChunk
from backend.app.main import create_app


@pytest.mark.anyio
async def test_retrieval_is_safely_unavailable_until_pgvector_is_explicitly_enabled(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite:///{(tmp_path / 'retrieval.db').as_posix()}",
            _env_file=None,
        )
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/retrieval/search", json={"query": "internal secret text"}
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "retrieval_not_available",
            "message": "Semantic retrieval is not available.",
        }
    }
    assert "internal secret text" not in response.text


@pytest.mark.anyio
async def test_retrieval_provider_failure_uses_safe_public_error() -> None:
    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql+psycopg://unused:unused@localhost/unused",
            embedding_enabled=True,
            _env_file=None,
        )
    )

    class FailingRetrievalService:
        def search(self, **_: object) -> list[RetrievedChunk]:
            raise EmbeddingProviderError("private provider payload")

    app.state.retrieval_service = FailingRetrievalService()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/retrieval/search", json={"query": "private provider payload"}
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "semantic_retrieval_unavailable",
            "message": "Semantic retrieval is temporarily unavailable.",
        }
    }
    assert "private provider payload" not in response.text
