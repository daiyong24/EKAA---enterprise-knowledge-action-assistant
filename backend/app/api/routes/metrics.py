"""Content-free Prometheus endpoint; no prompt, document, or user data is exposed."""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["observability"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        request.app.state.metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
