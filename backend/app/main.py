import asyncio

from fastapi import FastAPI
from sqlalchemy import text

from backend.app.api.request_size_limit import DocumentUploadRequestSizeLimitMiddleware
from backend.app.api.router import api_router
from backend.app.approval import ApprovalService
from backend.app.authorization.audit import InMemoryAuditSink
from backend.app.authorization.identity import (
    AnonymousIdentityProvider,
    IdentityProvider,
)
from backend.app.authorization.service import (
    AuthorizationService,
    AuthorizedRetrievalService,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.exceptions import register_exception_handlers
from backend.app.core.logging import configure_logging
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.embeddings.hybrid import HybridRetrievalService
from backend.app.embeddings.provider import (
    DeterministicHashEmbeddingProvider,
    EmbeddingProvider,
)
from backend.app.embeddings.service import (
    EmbeddingService,
    LexicalRetrievalService,
    SemanticRetrievalService,
)
from backend.app.ingestion.service import IngestionService
from backend.app.llm.policy import StageOneResponsePolicy
from backend.app.llm.prompt_builder import PromptBuilder
from backend.app.llm.provider import LLMProvider, OpenAICompatibleProvider
from backend.app.llm.service import ChatService
from backend.app.observability.metrics import MetricRegistry
from backend.app.observability.middleware import ObservabilityMiddleware
from backend.app.observability.tracing import InMemoryTraceExporter, Tracer
from backend.app.rag.citations import CitationOutputParser, CitationValidator
from backend.app.rag.context import ContextBuilder
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.rag.service import RagService
from backend.app.reliability.backoff import BackoffPolicy
from backend.app.reliability.circuit_breaker import CircuitBreaker
from backend.app.reliability.health import DependencyHealthService
from backend.app.reliability.provider import (
    CircuitBreakingProvider,
    is_provider_availability_failure,
)
from backend.app.reranking.provider import (
    DeterministicOverlapReranker,
    RerankerProvider,
)
from backend.app.reranking.service import RerankingService
from backend.app.security.rate_limit import InMemoryRateLimiter, RateLimitMiddleware


def create_app(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    reranker_provider: RerankerProvider | None = None,
    identity_provider: IdentityProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    application = FastAPI(
        title=resolved_settings.app_name,
        debug=resolved_settings.debug,
        version="0.1.0",
    )
    application.state.settings = resolved_settings
    metrics = MetricRegistry(max_series=resolved_settings.observability_max_series)
    trace_exporter = InMemoryTraceExporter(
        max_spans=resolved_settings.observability_max_spans
    )
    application.state.metrics = metrics
    application.state.trace_exporter = trace_exporter
    application.state.tracer = (
        Tracer(trace_exporter, sample_rate=resolved_settings.observability_sample_rate)
        if resolved_settings.observability_enabled
        else None
    )
    application.state.rate_limiter = InMemoryRateLimiter(
        capacity=resolved_settings.security_rate_limit_capacity,
        refill_per_second=resolved_settings.security_rate_limit_refill_per_second,
    )
    application.add_middleware(
        RateLimitMiddleware, limiter=application.state.rate_limiter
    )
    if resolved_settings.observability_enabled:
        application.add_middleware(
            ObservabilityMiddleware,
            tracer=application.state.tracer,
            metrics=metrics,
            api_prefix=resolved_settings.api_prefix,
        )
    application.add_middleware(
        DocumentUploadRequestSizeLimitMiddleware,
        path=f"{resolved_settings.api_prefix}/api/documents",
        max_body_size_bytes=resolved_settings.ingestion_max_request_size_bytes,
    )
    llm_provider = provider or OpenAICompatibleProvider(resolved_settings)
    backoff_policy = BackoffPolicy(
        base_seconds=resolved_settings.reliability_retry_base_seconds,
        maximum_seconds=resolved_settings.reliability_retry_max_seconds,
        jitter_ratio=resolved_settings.reliability_retry_jitter_ratio,
    )
    provider_breaker = CircuitBreaker(
        failure_threshold=resolved_settings.reliability_circuit_failure_threshold,
        recovery_timeout_seconds=resolved_settings.reliability_circuit_recovery_seconds,
        failure_predicate=is_provider_availability_failure,
    )
    reliable_llm_provider = CircuitBreakingProvider(llm_provider, provider_breaker)
    application.state.chat_service = ChatService(
        provider=reliable_llm_provider,
        prompt_builder=PromptBuilder(),
        response_policy=StageOneResponsePolicy(),
        max_retries=resolved_settings.llm_max_retries,
        retry_delay_seconds=resolved_settings.llm_retry_delay_seconds,
        backoff_policy=backoff_policy,
        deadline_seconds=resolved_settings.reliability_chat_deadline_seconds,
        tracer=application.state.tracer,
        metrics=metrics if resolved_settings.observability_enabled else None,
    )
    database_engine = create_database_engine(
        resolved_settings.database_url,
        connect_timeout_seconds=resolved_settings.database_connect_timeout_seconds,
        pool_timeout_seconds=resolved_settings.database_pool_timeout_seconds,
    )
    application.state.database_engine = database_engine
    embedding_model_provider = embedding_provider or DeterministicHashEmbeddingProvider(
        model=resolved_settings.embedding_model,
        dimensions=resolved_settings.embedding_dimensions,
    )
    session_factory = create_session_factory(database_engine)
    application.state.session_factory = session_factory
    application.state.audit_sink = InMemoryAuditSink()
    application.state.authorization_service = AuthorizationService(
        audit_sink=application.state.audit_sink
    )
    application.state.approval_service = ApprovalService()
    application.state.identity_provider = (
        identity_provider or AnonymousIdentityProvider()
    )
    application.state.ingestion_service = IngestionService(
        session_factory=session_factory,
        max_file_size_bytes=resolved_settings.ingestion_max_file_size_bytes,
        max_pdf_pages=resolved_settings.ingestion_max_pdf_pages,
        max_extracted_characters=resolved_settings.ingestion_max_extracted_characters,
        chunk_size=resolved_settings.ingestion_chunk_size_characters,
        chunk_overlap=resolved_settings.ingestion_chunk_overlap_characters,
    )
    application.state.embedding_service = EmbeddingService(
        session_factory=session_factory,
        provider=embedding_model_provider,
        model=resolved_settings.embedding_model,
        dimensions=resolved_settings.embedding_dimensions,
        batch_size=resolved_settings.embedding_batch_size,
    )
    semantic_retrieval_service = SemanticRetrievalService(
        session_factory=session_factory,
        provider=embedding_model_provider,
        model=resolved_settings.embedding_model,
        dimensions=resolved_settings.embedding_dimensions,
        max_top_k=resolved_settings.retrieval_max_top_k,
    )
    retrieval_service: SemanticRetrievalService | HybridRetrievalService = (
        semantic_retrieval_service
    )
    if resolved_settings.hybrid_retrieval_enabled:
        retrieval_service = HybridRetrievalService(
            semantic_service=semantic_retrieval_service,
            lexical_service=LexicalRetrievalService(
                session_factory=session_factory,
                max_top_k=resolved_settings.retrieval_max_top_k,
            ),
            semantic_candidate_count=resolved_settings.hybrid_semantic_candidate_count,
            lexical_candidate_count=resolved_settings.hybrid_lexical_candidate_count,
            rrf_rank_constant=resolved_settings.hybrid_rrf_rank_constant,
            max_top_k=resolved_settings.retrieval_max_top_k,
        )
    reranking_service = None
    if resolved_settings.reranking_enabled:
        reranking_service = RerankingService(
            provider=reranker_provider or DeterministicOverlapReranker(),
            timeout_seconds=resolved_settings.reranking_timeout_seconds,
        )
    application.state.retrieval_service = AuthorizedRetrievalService(
        retrieval_service=retrieval_service,
        authorization_service=application.state.authorization_service,
        session_factory=session_factory,
    )
    application.state.rag_service = RagService(
        retrieval_service=application.state.retrieval_service,
        provider=reliable_llm_provider,
        prompt_builder=RagPromptBuilder(
            direct_evidence_profile=resolved_settings.rag_direct_evidence_profile
        ),
        context_builder=ContextBuilder(
            max_characters=resolved_settings.rag_max_context_characters,
            max_estimated_tokens=resolved_settings.rag_max_context_tokens,
            max_sources=resolved_settings.rag_max_sources,
        ),
        reranking_service=reranking_service,
        citation_output_parser=CitationOutputParser(
            max_claims=resolved_settings.citation_max_claims,
            max_claim_characters=resolved_settings.citation_max_claim_characters,
        ),
        citation_validator=CitationValidator(
            max_excerpt_characters=resolved_settings.citation_max_excerpt_characters
        ),
        retrieval_top_k=resolved_settings.rag_retrieval_top_k,
        rerank_candidate_count=(
            resolved_settings.reranking_candidate_count
            if reranking_service is not None
            else None
        ),
        min_evidence_score=resolved_settings.rag_min_evidence_score,
        min_evidence_sources=resolved_settings.rag_min_evidence_sources,
        max_retries=resolved_settings.llm_max_retries,
        retry_delay_seconds=resolved_settings.llm_retry_delay_seconds,
        backoff_policy=backoff_policy,
        deadline_seconds=resolved_settings.reliability_rag_deadline_seconds,
        tracer=application.state.tracer,
        metrics=metrics if resolved_settings.observability_enabled else None,
    )

    async def database_health_check() -> None:
        def ping() -> None:
            with database_engine.connect() as connection:
                connection.execute(text("SELECT 1"))

        await asyncio.to_thread(ping)

    health_checks = {"database": database_health_check}
    provider_health_check = getattr(llm_provider, "health_check", None)
    if resolved_settings.llm_enabled and callable(provider_health_check):
        health_checks["llm_provider"] = provider_health_check
    application.state.dependency_health = DependencyHealthService(
        health_checks,
        timeout_seconds=resolved_settings.reliability_health_timeout_seconds,
    )
    register_exception_handlers(application)
    application.include_router(api_router, prefix=resolved_settings.api_prefix)
    return application


app = create_app()
