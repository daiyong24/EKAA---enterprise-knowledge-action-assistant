from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.main import create_app
from backend.app.observability.metrics import MetricRegistry
from backend.app.observability.redaction import redact_telemetry
from backend.app.observability.tracing import InMemoryTraceExporter, Tracer
from backend.app.rag.context import ContextBuilder
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.rag.service import RagService


def test_trace_propagates_parent_and_redacts_sensitive_attributes() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter)
    with (
        tracer.span(
            "root",
            correlation_id="corr-1",
            attributes={"prompt": "secret text", "route": "/health"},
        ),
        tracer.span("child", attributes={"route": "/api/chat"}),
    ):
        pass
    spans = exporter.snapshot()
    assert len(spans) == 2
    assert spans[0].trace_id == spans[1].trace_id
    root = next(span for span in spans if span.parent_span_id is None)
    child = next(span for span in spans if span.parent_span_id is not None)
    assert child.parent_span_id == root.span_id
    assert root.start_time <= child.start_time
    assert root.attributes["prompt"] == "[redacted]"
    assert root.attributes["route"] == "/health"


def test_sampling_and_exporter_outage_do_not_change_execution() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter, sample_rate=0)
    with tracer.span("unsampled"):
        pass
    assert exporter.snapshot() == ()

    @dataclass
    class BrokenExporter:
        def export(self, _span: object) -> None:
            raise RuntimeError("private exporter error")

    with Tracer(BrokenExporter()).span("outage"):
        pass


def test_metrics_are_prometheus_compatible_and_cardinality_bounded() -> None:
    registry = MetricRegistry(max_series=1)
    registry.inc("ekaa_requests_total", labels={"route": "/health"})
    registry.inc("ekaa_requests_total", labels={"route": "/other"})
    registry.observe("ekaa_latency_ms", 5.0, labels={"route": "/health"})
    rendered = registry.render_prometheus()
    assert 'ekaa_requests_total{route="/health"} 1' in rendered
    assert "/other" not in rendered
    assert "ekaa_latency_ms" not in rendered
    with pytest.raises(ValueError):
        registry.inc("bad-name", 1)
    with pytest.raises(ValueError):
        registry.inc("Δ_metric", 1)


def test_redaction_removes_secret_and_content_fields() -> None:
    value = redact_telemetry(
        {
            "api_key": "fake-value",
            "prompt": "private prompt",
            "route": "/api/chat",
            "nested": {"authorization": "Bearer abc"},
        }
    )
    assert value["api_key"] == "[redacted]"
    assert value["prompt"] == "[redacted]"
    assert value["route"] == "/api/chat"
    assert value["nested"]["authorization"] == "[redacted]"
    unknown = redact_telemetry(
        {"error": "private SSN 999-88-7777", "input": "customer content"}
    )
    assert unknown["error"] == "[redacted]"
    assert unknown["input"] == "[redacted]"


@pytest.mark.anyio
async def test_request_correlation_is_returned_and_metrics_are_content_free() -> None:
    app = create_app(Settings(environment="test", log_level="CRITICAL", _env_file=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health", headers={"x-correlation-id": "review-123"}
        )
        metrics = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "review-123"
    assert response.headers["x-trace-id"]
    assert "ekaa_http_requests_total" in metrics.text
    assert "review-123" not in metrics.text
    assert "prompt" not in metrics.text


@pytest.mark.anyio
async def test_unknown_raw_paths_use_safe_telemetry_labels() -> None:
    app = create_app(Settings(environment="test", log_level="CRITICAL", _env_file=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/raw-secret-document-name")
        metrics = await client.get("/metrics")
    assert response.status_code == 404
    assert "raw-secret-document-name" not in metrics.text
    unknown_spans = [
        span
        for span in app.state.trace_exporter.snapshot()
        if span.attributes.get("http.route") == "unknown"
    ]
    assert unknown_spans


@pytest.mark.anyio
async def test_disabled_observability_does_not_inject_application_tracer() -> None:
    app = create_app(
        Settings(
            environment="test",
            observability_enabled=False,
            log_level="CRITICAL",
            _env_file=None,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert app.state.tracer is None
    assert app.state.trace_exporter.snapshot() == ()


@pytest.mark.anyio
async def test_api_prefix_and_dynamic_routes_use_fixed_telemetry_labels() -> None:
    app = create_app(
        Settings(
            environment="test", api_prefix="/v1", log_level="CRITICAL", _env_file=None
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/v1/health")
        await client.get("/v1/api/approvals/approval-secret-123/approve")
        await client.get("/v1/api/admin/documents/document-secret-123/access")
    spans = app.state.trace_exporter.snapshot()
    labels = [span.attributes["http.route"] for span in spans]
    assert "/v1/health" in labels
    assert "/v1/api/approvals/{request_id}" in labels
    assert "/v1/api/admin/documents/{document_id}/access" in labels
    assert all("secret-123" not in str(span.attributes) for span in spans)


@pytest.mark.anyio
async def test_rate_limit_returns_safe_429() -> None:
    app = create_app(
        Settings(
            environment="test",
            security_rate_limit_capacity=1,
            log_level="CRITICAL",
            _env_file=None,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/health")
        second = await client.get("/health")
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {
        "error": {"code": "rate_limited", "message": "Too many requests."}
    }


@pytest.mark.anyio
async def test_chat_and_rag_injected_metrics_record_lifecycle_outcomes() -> None:
    class FakeProvider:
        async def complete(self, _prompt: ChatPrompt) -> ChatResult:
            return ChatResult(
                answer="deterministic answer",
                model="fake",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    from pydantic import SecretStr

    app = create_app(
        Settings(
            environment="test",
            llm_enabled=True,
            llm_api_key=SecretStr("fake-value"),
            llm_model="fake",
            log_level="CRITICAL",
            _env_file=None,
        ),
        FakeProvider(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/chat", json={"message": "Explain RAG."})
        metrics = await client.get("/metrics")
    assert response.status_code == 200
    assert (
        'ekaa_llm_operations_total{operation="chat",outcome="success"} 1'
        in metrics.text
    )

    class EmptyRetrieval:
        def search(self, *, query: str, top_k: int) -> list[object]:
            return []

    rag = RagService(
        retrieval_service=EmptyRetrieval(),
        provider=object(),  # type: ignore[arg-type]
        prompt_builder=RagPromptBuilder(),
        context_builder=ContextBuilder(
            max_characters=1_000, max_estimated_tokens=250, max_sources=2
        ),
        retrieval_top_k=1,
        min_evidence_score=0.2,
        min_evidence_sources=1,
        max_retries=0,
        retry_delay_seconds=0,
        metrics=app.state.metrics,
        tracer=None,
    )
    await rag.answer("question")
    rendered = app.state.metrics.render_prometheus()
    assert (
        'ekaa_rag_operations_total{operation="answer",outcome="success"} 1' in rendered
    )
