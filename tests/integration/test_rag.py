import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
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


def _result(answer: str) -> ChatResult:
    return ChatResult(
        answer=answer,
        model="fake-rag-model",
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


@pytest.mark.anyio
async def test_rag_returns_grounded_answer_and_only_retrieval_derived_sources() -> None:
    provider = FakeProvider(_result("The recovery target is four hours."))

    status, payload = await _post(_app(provider, [_source()]), {"question": "Target?"})

    assert status == 200
    assert payload == {
        "answer": "The recovery target is four hours.",
        "model": "fake-rag-model",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "sources": [
            {
                "document_id": "doc-real",
                "document_name": "synthetic.md",
                "chunk_id": "chunk-real",
                "chunk_index": 2,
                "page_number": 3,
                "section": "Recovery",
            }
        ],
    }


@pytest.mark.anyio
async def test_rag_rejects_model_invented_source_ids() -> None:
    provider = FakeProvider(_result("Answer [source: fabricated-chunk-id]."))

    status, payload = await _post(_app(provider, [_source()]), {"question": "Target?"})

    assert status == 502
    assert payload == {
        "error": {
            "code": "rag_invalid_generation",
            "message": "The language model returned an invalid response.",
        }
    }
    assert "fabricated-chunk-id" not in str(payload)


@pytest.mark.anyio
async def test_rag_no_evidence_returns_exact_refusal_without_provider_call() -> None:
    provider = FakeProvider(_result("unused"))

    status, payload = await _post(_app(provider, []), {"question": "Target?"})

    assert status == 200
    assert payload == {
        "answer": "Insufficient information is available in the knowledge base.",
        "model": "ekaa-rag-evidence-policy",
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "sources": [],
    }
    assert provider.calls == []


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
        "sources": [],
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
