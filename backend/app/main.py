from fastapi import FastAPI

from backend.app.api.request_size_limit import DocumentUploadRequestSizeLimitMiddleware
from backend.app.api.router import api_router
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
from backend.app.rag.citations import CitationOutputParser, CitationValidator
from backend.app.rag.context import ContextBuilder
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.rag.service import RagService


def create_app(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    application = FastAPI(
        title=resolved_settings.app_name,
        debug=resolved_settings.debug,
        version="0.1.0",
    )
    application.state.settings = resolved_settings
    application.add_middleware(
        DocumentUploadRequestSizeLimitMiddleware,
        path=f"{resolved_settings.api_prefix}/api/documents",
        max_body_size_bytes=resolved_settings.ingestion_max_request_size_bytes,
    )
    llm_provider = provider or OpenAICompatibleProvider(resolved_settings)
    application.state.chat_service = ChatService(
        provider=llm_provider,
        prompt_builder=PromptBuilder(),
        response_policy=StageOneResponsePolicy(),
        max_retries=resolved_settings.llm_max_retries,
        retry_delay_seconds=resolved_settings.llm_retry_delay_seconds,
    )
    database_engine = create_database_engine(resolved_settings.database_url)
    application.state.database_engine = database_engine
    embedding_model_provider = embedding_provider or DeterministicHashEmbeddingProvider(
        model=resolved_settings.embedding_model,
        dimensions=resolved_settings.embedding_dimensions,
    )
    session_factory = create_session_factory(database_engine)
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
    application.state.retrieval_service = semantic_retrieval_service
    if resolved_settings.hybrid_retrieval_enabled:
        application.state.retrieval_service = HybridRetrievalService(
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
    application.state.rag_service = RagService(
        retrieval_service=application.state.retrieval_service,
        provider=llm_provider,
        prompt_builder=RagPromptBuilder(),
        context_builder=ContextBuilder(
            max_characters=resolved_settings.rag_max_context_characters,
            max_estimated_tokens=resolved_settings.rag_max_context_tokens,
            max_sources=resolved_settings.rag_max_sources,
        ),
        citation_output_parser=CitationOutputParser(
            max_claims=resolved_settings.citation_max_claims,
            max_claim_characters=resolved_settings.citation_max_claim_characters,
        ),
        citation_validator=CitationValidator(
            max_excerpt_characters=resolved_settings.citation_max_excerpt_characters
        ),
        retrieval_top_k=resolved_settings.rag_retrieval_top_k,
        min_evidence_score=resolved_settings.rag_min_evidence_score,
        min_evidence_sources=resolved_settings.rag_min_evidence_sources,
        max_retries=resolved_settings.llm_max_retries,
        retry_delay_seconds=resolved_settings.llm_retry_delay_seconds,
    )
    register_exception_handlers(application)
    application.include_router(api_router, prefix=resolved_settings.api_prefix)
    return application


app = create_app()
