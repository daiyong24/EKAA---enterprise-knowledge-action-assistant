import logging
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
INTERNAL_ERROR_MESSAGE: Final = "An unexpected error occurred."


class ApplicationError(Exception):
    """Expected application failure safe to expose through a stable code."""

    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def handle_application_error(
        request: Request, exc: ApplicationError
    ) -> JSONResponse:
        logger.warning(
            "application_error method=%s path=%s code=%s",
            request.method,
            request.url.path,
            exc.code,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unexpected_error method=%s path=%s error_type=%s",
            request.method,
            request.url.path,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": INTERNAL_ERROR_MESSAGE,
                }
            },
        )
