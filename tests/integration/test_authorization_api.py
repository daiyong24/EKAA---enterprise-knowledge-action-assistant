from collections.abc import Sequence
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.authorization.identity import StaticTokenIdentityProvider
from backend.app.authorization.models import Principal
from backend.app.authorization.service import AuthorizedRetrievalService
from backend.app.core.config import Settings
from backend.app.db.models import Base, Document
from backend.app.embeddings.models import RetrievedChunk
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.main import create_app
from backend.app.rag.context import ContextBuilder
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.rag.service import RagService


def _app(tmp_path: Path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}",
        _env_file=None,
    )
    app = create_app(
        settings,
        identity_provider=StaticTokenIdentityProvider(
            {
                "admin-token": Principal("admin-user", roles=frozenset({"admin"})),
                "user-token": Principal("normal-user"),
            }
        ),
    )
    Base.metadata.create_all(app.state.database_engine)
    with app.state.session_factory.begin() as session:
        session.add(
            Document(
                id="doc-1",
                filename="synthetic.md",
                mime_type="text/markdown",
                checksum="a" * 64,
                status="completed",
            ),
            Document(
                id="secret-1",
                filename="secret.md",
                mime_type="text/markdown",
                checksum="b" * 64,
                status="completed",
                classification="confidential",
                allowed_groups=["finance"],
            ),
        )
    return app


class _ScopedRetrieval:
    def __init__(self) -> None:
        self.allowed: list[Sequence[str] | None] = []
        self.sources = [
            RetrievedChunk(
                document_id="doc-1",
                document_name="synthetic.md",
                chunk_id="chunk-public",
                chunk_index=0,
                content="Public evidence is available.",
                score=0.9,
                page_number=None,
                section=None,
            ),
            RetrievedChunk(
                document_id="secret-1",
                document_name="secret.md",
                chunk_id="chunk-secret",
                chunk_index=0,
                content="Secret finance evidence must never leak.",
                score=0.9,
                page_number=None,
                section=None,
            ),
        ]

    def search(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        del query
        self.allowed.append(allowed_document_ids)
        allowed = set(allowed_document_ids or ())
        return [item for item in self.sources if item.document_id in allowed][:top_k]


class _GroundedProvider:
    model = "test-model"

    def __init__(self) -> None:
        self.prompts: list[ChatPrompt] = []

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        self.prompts.append(prompt)
        return ChatResult(
            answer=(
                '{"claims":[{"text":"Public evidence is available.",'
                '"citation_chunk_ids":["chunk-public"]}]}'
            ),
            model=self.model,
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


@pytest.mark.anyio
async def test_admin_policy_update_requires_admin_and_returns_metadata_only(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.put(
            "/api/admin/documents/doc-1/access",
            headers={"Authorization": "Bearer user-token"},
            json={
                "classification": "confidential",
                "allowed_groups": [" finance ", "finance"],
            },
        )
        denied_missing = await client.put(
            "/api/admin/documents/does-not-exist/access",
            headers={"Authorization": "Bearer user-token"},
            json={"classification": "confidential", "allowed_groups": ["finance"]},
        )
        invalid_acl = await client.put(
            "/api/admin/documents/doc-1/access",
            headers={"Authorization": "Bearer admin-token"},
            json={"classification": "confidential", "allowed_groups": [""]},
        )
        invalid_classification = await client.put(
            "/api/admin/documents/doc-1/access",
            headers={"Authorization": "Bearer admin-token"},
            json={"classification": "top-secret", "allowed_groups": []},
        )
        allowed = await client.put(
            "/api/admin/documents/doc-1/access",
            headers={"Authorization": "Bearer admin-token"},
            json={
                "classification": "confidential",
                "allowed_groups": [" finance ", "finance"],
            },
        )
    assert denied.status_code == 403
    assert denied_missing.status_code == denied.status_code
    assert denied_missing.json() == denied.json()
    assert invalid_acl.status_code == 422
    assert invalid_classification.status_code == 422
    assert allowed.status_code == 200
    assert allowed.json() == {
        "document_id": "doc-1",
        "classification": "confidential",
        "allowed_groups": ["finance"],
    }
    assert "synthetic" not in allowed.text
    with app.state.session_factory() as session:
        persisted = session.get(Document, "doc-1")
        assert persisted is not None
        assert persisted.allowed_groups == ["finance"]
    events = app.state.audit_sink.snapshot()
    assert any(event.outcome == "deny" for event in events)
    assert any(event.outcome == "allow" for event in events)


@pytest.mark.anyio
async def test_http_retrieval_and_rag_are_scoped_before_content_selection(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    app.state.settings.embedding_enabled = True
    app.state.settings.rag_enabled = True
    delegate = _ScopedRetrieval()
    app.state.retrieval_service = AuthorizedRetrievalService(
        retrieval_service=delegate,
        authorization_service=app.state.authorization_service,
        session_factory=app.state.session_factory,
    )
    provider = _GroundedProvider()
    app.state.rag_service = RagService(
        retrieval_service=app.state.retrieval_service,
        provider=provider,
        prompt_builder=RagPromptBuilder(),
        context_builder=ContextBuilder(
            max_characters=1_000, max_estimated_tokens=300, max_sources=3
        ),
        retrieval_top_k=3,
        min_evidence_score=0.5,
        min_evidence_sources=1,
        max_retries=0,
        retry_delay_seconds=0,
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        anonymous = await client.post(
            "/api/retrieval/search", json={"query": "private-query"}
        )
        normal = await client.post(
            "/api/retrieval/search",
            headers={
                "Authorization": "Bearer user-token",
                "X-Correlation-ID": "corr-123",
            },
            json={"query": "private-query"},
        )
        rag = await client.post(
            "/api/rag/answer",
            headers={"Authorization": "Bearer user-token"},
            json={"question": "provider-payload"},
        )
        invalid = await client.post(
            "/api/retrieval/search",
            headers={"Authorization": "Bearer invalid"},
            json={"query": "private-query"},
        )
    assert anonymous.status_code == 200
    assert [item["document_id"] for item in anonymous.json()["sources"]] == ["doc-1"]
    assert normal.status_code == 200
    assert [item["document_id"] for item in normal.json()["sources"]] == ["doc-1"]
    assert rag.status_code == 200
    assert "Secret finance evidence" not in rag.text
    assert provider.prompts
    assert all(
        "Secret finance evidence" not in prompt.system + prompt.user
        for prompt in provider.prompts
    )
    assert invalid.status_code == 401
    assert all(
        all(
            value not in str(event.__dict__)
            for value in (
                "finance",
                "private-query",
                "provider-payload",
                "Secret finance evidence",
            )
        )
        for event in app.state.audit_sink.snapshot()
    )
    assert any(
        event.correlation_id == "corr-123" for event in app.state.audit_sink.snapshot()
    )
