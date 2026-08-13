import json

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.embeddings.hybrid import HybridRetrievalService
from backend.app.embeddings.models import RetrievedChunk
from backend.app.llm.errors import ProviderTimeoutError
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.main import create_app
from backend.app.rag.context import ContextBuilder
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.rag.service import RagService


class FakeRetrievalService:
    def __init__(self, sources: list[RetrievedChunk]) -> None:
        self.sources = sources

    def search(self, *, query: str, top_k: int) -> list[RetrievedChunk]:
        return self.sources


class FakeProvider:
    def __init__(self, outcome: ChatResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[ChatPrompt] = []

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        self.calls.append(prompt)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class HybridSemanticFixture:
    def __init__(self, sources: list[RetrievedChunk]) -> None:
        self.sources = sources

    def search(self, **_: object) -> list[RetrievedChunk]:
        return self.sources


class HybridLexicalFixture:
    def __init__(self, sources: list[RetrievedChunk]) -> None:
        self.sources = sources

    def search_with_metadata(self, **_: object) -> tuple[list[RetrievedChunk], int]:
        return self.sources, 0


def _source(*, score: float = 0.95) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-real",
        document_name="synthetic.md",
        chunk_id="chunk-real",
        chunk_index=2,
        content="Synthetic evidence: the recovery target is four hours.",
        score=score,
        page_number=3,
        section="Recovery",
    )


def _approval_source() -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-approval",
        document_name="synthetic-approval.md",
        chunk_id="chunk-real",
        chunk_index=0,
        content="Alice approved the deployment plan.",
        score=0.95,
        page_number=None,
        section="Approvals",
    )


def _negative_source(content: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-negative",
        document_name="synthetic-negative.md",
        chunk_id="chunk-real",
        chunk_index=0,
        content=content,
        score=0.95,
        page_number=None,
        section="Policy",
    )


def _settings(*, rag_enabled: bool = True) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        embedding_enabled=True,
        llm_enabled=True,
        llm_api_key="not-a-real-secret",
        llm_model="fake-rag-model",
        rag_enabled=rag_enabled,
        _env_file=None,
    )


def _app(
    provider: FakeProvider,
    sources: list[RetrievedChunk],
    *,
    context_builder: ContextBuilder | None = None,
) -> object:
    app = create_app(_settings(), provider)
    app.state.rag_service = RagService(
        retrieval_service=FakeRetrievalService(sources),
        provider=provider,
        prompt_builder=RagPromptBuilder(),
        context_builder=context_builder
        or ContextBuilder(
            max_characters=1_000, max_estimated_tokens=300, max_sources=3
        ),
        retrieval_top_k=3,
        min_evidence_score=0.5,
        min_evidence_sources=1,
        max_retries=0,
        retry_delay_seconds=0,
    )
    return app


async def _post(app: object, body: object) -> tuple[int, dict[str, object]]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/rag/answer", json=body)
    return response.status_code, response.json()


def _result(
    answer: str,
    *,
    citation_chunk_ids: list[str] | None = None,
) -> ChatResult:
    return ChatResult(
        answer=json.dumps(
            {
                "claims": [
                    {
                        "text": answer,
                        "citation_chunk_ids": citation_chunk_ids or ["chunk-real"],
                    }
                ]
            }
        ),
        model="fake-rag-model",
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _empty_result() -> ChatResult:
    return ChatResult(
        answer='{"claims":[]}',
        model="fake-rag-model",
        usage=TokenUsage(input_tokens=10, output_tokens=1, total_tokens=11),
    )


@pytest.mark.anyio
async def test_rag_returns_grounded_answer_and_server_derived_citations() -> None:
    provider = FakeProvider(_result("The recovery target is four hours."))

    status, payload = await _post(_app(provider, [_source()]), {"question": "Target?"})

    assert status == 200
    assert payload == {
        "answer": "The recovery target is four hours.",
        "model": "fake-rag-model",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "claims": [
            {
                "text": "The recovery target is four hours.",
                "status": "supported",
                "citation_ids": ["citation-1"],
                "unsupported_reason": None,
            }
        ],
        "citations": [
            {
                "citation_id": "citation-1",
                "document_id": "doc-real",
                "document_name": "synthetic.md",
                "chunk_id": "chunk-real",
                "chunk_index": 2,
                "excerpt": "Synthetic evidence: the recovery target is four hours.",
                "page_number": 3,
                "section": "Recovery",
            }
        ],
    }


@pytest.mark.anyio
async def test_hybrid_evidence_score_reaches_rag_provider_and_citation_api() -> None:
    source = _source(score=0.95)
    hybrid = HybridRetrievalService(
        semantic_service=HybridSemanticFixture([source]),  # type: ignore[arg-type]
        lexical_service=HybridLexicalFixture([source]),  # type: ignore[arg-type]
        semantic_candidate_count=1,
        lexical_candidate_count=1,
        rrf_rank_constant=60,
        max_top_k=20,
    )
    provider = FakeProvider(_result("The recovery target is four hours."))
    app = create_app(_settings(), provider)
    app.state.rag_service = RagService(
        retrieval_service=hybrid,
        provider=provider,
        prompt_builder=RagPromptBuilder(),
        context_builder=ContextBuilder(
            max_characters=1_000, max_estimated_tokens=300, max_sources=3
        ),
        retrieval_top_k=3,
        min_evidence_score=0.2,
        min_evidence_sources=1,
        max_retries=0,
        retry_delay_seconds=0,
    )

    status, payload = await _post(app, {"question": "What is the target?"})

    assert status == 200
    assert provider.calls
    assert payload["claims"] == [
        {
            "text": "The recovery target is four hours.",
            "status": "supported",
            "citation_ids": ["citation-1"],
            "unsupported_reason": None,
        }
    ]
    assert payload["citations"][0]["chunk_id"] == "chunk-real"  # type: ignore[index]


@pytest.mark.anyio
async def test_hybrid_low_confidence_evidence_remains_safe_at_default_threshold() -> (
    None
):
    low_source = _source(score=0.19)
    hybrid = HybridRetrievalService(
        semantic_service=HybridSemanticFixture([low_source]),  # type: ignore[arg-type]
        lexical_service=HybridLexicalFixture([]),  # type: ignore[arg-type]
        semantic_candidate_count=1,
        lexical_candidate_count=1,
        rrf_rank_constant=60,
        max_top_k=20,
    )
    provider = FakeProvider(_result("The recovery target is four hours."))
    app = create_app(_settings(), provider)
    app.state.rag_service = RagService(
        retrieval_service=hybrid,
        provider=provider,
        prompt_builder=RagPromptBuilder(),
        context_builder=ContextBuilder(
            max_characters=1_000, max_estimated_tokens=300, max_sources=3
        ),
        retrieval_top_k=3,
        min_evidence_score=0.2,
        min_evidence_sources=1,
        max_retries=0,
        retry_delay_seconds=0,
    )

    status, payload = await _post(app, {"question": "What is the target?"})

    assert status == 200
    assert provider.calls == []
    assert payload["claims"] == []
    assert payload["citations"] == []


@pytest.mark.anyio
async def test_hybrid_lexical_only_non_exact_low_score_remains_safe() -> None:
    # A raw non-exact FTS rank is not promoted merely because it is the only branch.
    lexical_only_source = _source(score=0.19)
    hybrid = HybridRetrievalService(
        semantic_service=HybridSemanticFixture([]),  # type: ignore[arg-type]
        lexical_service=HybridLexicalFixture([lexical_only_source]),  # type: ignore[arg-type]
        semantic_candidate_count=1,
        lexical_candidate_count=1,
        rrf_rank_constant=60,
        max_top_k=20,
    )
    provider = FakeProvider(_result("The recovery target is four hours."))
    app = create_app(_settings(), provider)
    app.state.rag_service = RagService(
        retrieval_service=hybrid,
        provider=provider,
        prompt_builder=RagPromptBuilder(),
        context_builder=ContextBuilder(
            max_characters=1_000, max_estimated_tokens=300, max_sources=3
        ),
        retrieval_top_k=3,
        min_evidence_score=0.2,
        min_evidence_sources=1,
        max_retries=0,
        retry_delay_seconds=0,
    )

    status, payload = await _post(app, {"question": "What is the target?"})

    assert status == 200
    assert provider.calls == []
    assert payload["claims"] == []
    assert payload["citations"] == []


@pytest.mark.anyio
async def test_rag_flags_invented_chunk_ids() -> None:
    provider = FakeProvider(
        _result(
            "The recovery target is four hours.",
            citation_chunk_ids=["fabricated-chunk-id"],
        )
    )

    status, payload = await _post(_app(provider, [_source()]), {"question": "Target?"})

    assert status == 200
    assert payload == {
        "answer": "Insufficient information is available in the knowledge base.",
        "model": "ekaa-rag-evidence-policy",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "claims": [
            {
                "text": "The recovery target is four hours.",
                "status": "unsupported",
                "citation_ids": [],
                "unsupported_reason": "citation_not_selected",
            }
        ],
        "citations": [],
    }
    assert "fabricated-chunk-id" not in str(payload)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source", "claim"),
    [
        (_approval_source(), "The deployment plan approved Alice."),
        (_source(), "The recovery recovery target is four hours."),
    ],
)
async def test_rag_rejects_reordered_or_repeated_claim_tokens(
    source: RetrievedChunk, claim: str
) -> None:
    provider = FakeProvider(_result(claim))

    status, payload = await _post(_app(provider, [source]), {"question": "Target?"})

    assert status == 200
    assert (
        payload["answer"]
        == "Insufficient information is available in the knowledge base."
    )
    assert payload["model"] == "ekaa-rag-evidence-policy"
    assert payload["citations"] == []
    assert payload["claims"] == [
        {
            "text": claim,
            "status": "unsupported",
            "citation_ids": [],
            "unsupported_reason": "citation_does_not_support_claim",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "claim"),
    [
        (
            "Alice did not approve the deployment plan.",
            "Alice approved the deployment plan.",
        ),
        ("No deployment approval is permitted.", "Deployment approval is permitted."),
        (
            "Alice never approved the deployment plan.",
            "Alice approved the deployment plan.",
        ),
        ("The service runs without approval.", "The service runs approval."),
        (
            "Alice didn't approve the deployment plan.",
            "Alice approved the deployment plan.",
        ),
        ("The service can't process requests.", "The service process requests."),
    ],
)
async def test_rag_rejects_positive_claims_against_negative_evidence(
    content: str, claim: str
) -> None:
    provider = FakeProvider(_result(claim))

    status, payload = await _post(
        _app(provider, [_negative_source(content)]), {"question": "Status?"}
    )

    assert status == 200
    assert (
        payload["answer"]
        == "Insufficient information is available in the knowledge base."
    )
    assert payload["model"] == "ekaa-rag-evidence-policy"
    assert payload["citations"] == []
    assert payload["claims"] == [
        {
            "text": claim,
            "status": "unsupported",
            "citation_ids": [],
            "unsupported_reason": "citation_does_not_support_claim",
        }
    ]


@pytest.mark.anyio
async def test_rag_no_evidence_returns_exact_refusal_without_provider_call() -> None:
    provider = FakeProvider(_result("unused"))

    status, payload = await _post(_app(provider, []), {"question": "Target?"})

    assert status == 200
    assert payload == {
        "answer": "Insufficient information is available in the knowledge base.",
        "model": "ekaa-rag-evidence-policy",
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "claims": [],
        "citations": [],
    }
    assert provider.calls == []


@pytest.mark.anyio
async def test_rag_empty_provider_claims_return_safe_refusal_with_honest_usage() -> (
    None
):
    provider = FakeProvider(_empty_result())

    status, payload = await _post(_app(provider, [_source()]), {"question": "Target?"})

    assert status == 200
    assert payload == {
        "answer": "Insufficient information is available in the knowledge base.",
        "model": "ekaa-rag-evidence-policy",
        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        "claims": [],
        "citations": [],
    }


@pytest.mark.anyio
async def test_rag_below_threshold_or_context_excluded_evidence_refuses() -> None:
    provider = FakeProvider(_result("unused"))
    below_threshold_status, below_threshold_payload = await _post(
        _app(provider, [_source(score=0.49)]), {"question": "Target?"}
    )
    capacity_status, capacity_payload = await _post(
        _app(
            provider,
            [_source()],
            context_builder=ContextBuilder(
                max_characters=1, max_estimated_tokens=1, max_sources=3
            ),
        ),
        {"question": "Target?"},
    )

    expected = {
        "answer": "Insufficient information is available in the knowledge base.",
        "model": "ekaa-rag-evidence-policy",
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "claims": [],
        "citations": [],
    }
    assert below_threshold_status == 200
    assert below_threshold_payload == expected
    assert capacity_status == 200
    assert capacity_payload == expected
    assert provider.calls == []


@pytest.mark.anyio
async def test_rag_provider_failure_has_safe_error_contract() -> None:
    private_detail = "private provider payload"
    provider = FakeProvider(ProviderTimeoutError(private_detail))

    status, payload = await _post(
        _app(provider, [_source()]), {"question": private_detail}
    )

    assert status == 503
    assert payload == {
        "error": {
            "code": "rag_generation_unavailable",
            "message": "Evidence-bounded answer generation is temporarily unavailable.",
        }
    }
    assert private_detail not in str(payload)


@pytest.mark.anyio
async def test_rag_is_safely_unavailable_when_disabled() -> None:
    provider = FakeProvider(_result("unused"))
    app = create_app(_settings(rag_enabled=False), provider)

    status, payload = await _post(app, {"question": "Question?"})

    assert status == 503
    assert payload == {
        "error": {
            "code": "rag_not_available",
            "message": "Evidence-bounded answers are not available.",
        }
    }
    assert provider.calls == []
