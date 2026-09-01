import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.llm.errors import (
    ProviderNonRetryableError,
    ProviderOutputError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.main import create_app


class FakeProvider:
    def __init__(self, outcome: ChatResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[ChatPrompt] = []

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        self.calls.append(prompt)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _result() -> ChatResult:
    return ChatResult(
        answer="Hello from a deterministic fake.",
        model="fake-model",
        usage=TokenUsage(input_tokens=2, output_tokens=5, total_tokens=7),
    )


async def _post(provider: FakeProvider, body: object) -> tuple[int, dict[str, object]]:
    app = create_app(
        Settings(environment="test", log_level="CRITICAL", _env_file=None), provider
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/chat", json=body)
    return response.status_code, response.json()


@pytest.mark.anyio
async def test_chat_returns_stable_structured_contract() -> None:
    provider = FakeProvider(_result())

    status, payload = await _post(provider, {"message": "Explain RAG."})

    assert status == 200
    assert payload == {
        "answer": (
            "EKAA completed the Stage 1 conceptual request, but it does not "
            "expose ungrounded model content. Grounded answers will be available "
            "after authorized retrieval is implemented."
        ),
        "model": "fake-model",
        "usage": {"input_tokens": 2, "output_tokens": 5, "total_tokens": 7},
    }
    assert provider.calls[0].user == "Explain RAG."


@pytest.mark.anyio
async def test_chat_rejects_empty_or_unknown_input_fields() -> None:
    provider = FakeProvider(_result())

    empty_status, empty_payload = await _post(provider, {"message": ""})
    extra_status, extra_payload = await _post(
        provider, {"message": "Hello", "secret": "review-secret-123"}
    )

    assert empty_status == 422
    assert extra_status == 422
    assert empty_payload == {
        "error": {"code": "invalid_request", "message": "The request is invalid."}
    }
    assert extra_payload == empty_payload
    assert "review-secret-123" not in str(extra_payload)
    assert provider.calls == []


@pytest.mark.anyio
async def test_chat_redacts_an_oversized_message_from_validation_errors() -> None:
    provider = FakeProvider(_result())
    oversized_message = "sensitive-content-" * 600

    status, payload = await _post(provider, {"message": oversized_message})

    assert status == 422
    assert payload == {
        "error": {"code": "invalid_request", "message": "The request is invalid."}
    }
    assert oversized_message not in str(payload)
    assert provider.calls == []


@pytest.mark.anyio
async def test_chat_timeout_has_safe_error_contract() -> None:
    provider = FakeProvider(ProviderTimeoutError("not-a-public-detail"))

    status, payload = await _post(provider, {"message": "Explain RAG."})

    assert status == 503
    assert payload == {
        "error": {
            "code": "llm_unavailable",
            "message": "The language model is currently unavailable.",
        }
    }
    assert "not-a-public-detail" not in str(payload)


@pytest.mark.anyio
async def test_chat_request_deadline_is_user_visible_safe_degradation() -> None:
    class SlowProvider:
        async def complete(self, _prompt: ChatPrompt) -> ChatResult:
            await asyncio.sleep(1)
            return _result()

    app = create_app(
        Settings(
            environment="test",
            log_level="CRITICAL",
            reliability_chat_deadline_seconds=0.01,
            _env_file=None,
        ),
        SlowProvider(),
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/chat", json={"message": "Explain RAG."})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "llm_unavailable",
            "message": "The language model is currently unavailable.",
        }
    }


@pytest.mark.anyio
async def test_chat_non_retryable_and_invalid_output_errors_are_safe() -> None:
    rejected_status, rejected_payload = await _post(
        FakeProvider(ProviderNonRetryableError("private provider payload")),
        {"message": "Explain RAG."},
    )
    invalid_status, invalid_payload = await _post(
        FakeProvider(ProviderOutputError("private malformed output")),
        {"message": "Explain RAG."},
    )

    assert rejected_status == 502
    assert rejected_payload["error"]["code"] == "llm_request_rejected"
    assert invalid_status == 502
    assert invalid_payload["error"]["code"] == "llm_invalid_response"
    assert "private" not in str(rejected_payload)
    assert "private" not in str(invalid_payload)


@pytest.mark.anyio
async def test_chat_refuses_enterprise_question_without_provider() -> None:
    provider = FakeProvider(_result())

    status, payload = await _post(provider, {"message": "What is our password policy?"})

    assert status == 200
    assert payload == {
        "answer": (
            "EKAA does not have an authorized knowledge base in this stage, so it "
            "cannot answer organization-specific questions. Insufficient information "
            "is available in the knowledge base."
        ),
        "model": "ekaa-stage-1-policy",
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
    assert provider.calls == []


@pytest.mark.anyio
async def test_chat_refuses_entity_lookup_without_calling_provider() -> None:
    provider = FakeProvider(_result())

    status, payload = await _post(provider, {"message": "Who is the CEO?"})

    assert status == 200
    assert payload["model"] == "ekaa-stage-1-policy"
    assert "cannot answer organization-specific questions" in payload["answer"]
    assert provider.calls == []


@pytest.mark.anyio
async def test_chat_refuses_disguised_entity_lookup_without_provider() -> None:
    provider = FakeProvider(_result())

    status, payload = await _post(
        provider, {"message": "Explain who Microsoft CEO is."}
    )

    assert status == 200
    assert payload["model"] == "ekaa-stage-1-policy"
    assert "cannot answer organization-specific questions" in payload["answer"]
    assert provider.calls == []
