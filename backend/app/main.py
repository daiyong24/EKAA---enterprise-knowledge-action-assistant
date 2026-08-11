from fastapi import FastAPI

from backend.app.api.request_size_limit import DocumentUploadRequestSizeLimitMiddleware
from backend.app.api.router import api_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.exceptions import register_exception_handlers
from backend.app.core.logging import configure_logging
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.ingestion.service import IngestionService
from backend.app.llm.policy import StageOneResponsePolicy
from backend.app.llm.prompt_builder import PromptBuilder
from backend.app.llm.provider import LLMProvider, OpenAICompatibleProvider
from backend.app.llm.service import ChatService


def create_app(
    settings: Settings | None = None, provider: LLMProvider | None = None
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    application = FastAPI(
        title=resolved_settings.app_name,
        debug=resolved_settings.debug,
        version="0.1.0",
    )
    application.add_middleware(
        DocumentUploadRequestSizeLimitMiddleware,
        path=f"{resolved_settings.api_prefix}/api/documents",
        max_body_size_bytes=resolved_settings.ingestion_max_request_size_bytes,
    )
    application.state.chat_service = ChatService(
        provider=provider or OpenAICompatibleProvider(resolved_settings),
        prompt_builder=PromptBuilder(),
        response_policy=StageOneResponsePolicy(),
        max_retries=resolved_settings.llm_max_retries,
        retry_delay_seconds=resolved_settings.llm_retry_delay_seconds,
    )
    database_engine = create_database_engine(resolved_settings.database_url)
    application.state.database_engine = database_engine
    application.state.ingestion_service = IngestionService(
        session_factory=create_session_factory(database_engine),
        max_file_size_bytes=resolved_settings.ingestion_max_file_size_bytes,
        max_pdf_pages=resolved_settings.ingestion_max_pdf_pages,
        max_extracted_characters=resolved_settings.ingestion_max_extracted_characters,
        chunk_size=resolved_settings.ingestion_chunk_size_characters,
        chunk_overlap=resolved_settings.ingestion_chunk_overlap_characters,
    )
    register_exception_handlers(application)
    application.include_router(api_router, prefix=resolved_settings.api_prefix)
    return application


app = create_app()
