"""ASGI boundary that caps a document-upload request before multipart parsing."""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class DocumentUploadRequestSizeLimitMiddleware:
    """Buffer only an allowed request body, then replay it to the application.

    The middleware runs before Starlette's multipart parser. It rejects both an
    oversized Content-Length and a stream that exceeds the cap when Content-Length
    is absent, malformed, or falsely small. Thus the multipart parser never gets an
    unbounded upload to spool.
    """

    def __init__(self, app: ASGIApp, *, path: str, max_body_size_bytes: int) -> None:
        self.app = app
        self._path = path
        self._max_body_size_bytes = max_body_size_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self._path:
            await self.app(scope, receive, send)
            return
        content_length = _content_length(scope)
        if content_length is not None and content_length > self._max_body_size_bytes:
            await _send_too_large(send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self._max_body_size_bytes:
                await _send_too_large(send)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


async def _send_too_large(send: Send) -> None:
    logger.warning("document_request_too_large")
    payload = (
        b'{"error":{"code":"document_too_large",'
        b'"message":"The document exceeds the allowed size."}}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
