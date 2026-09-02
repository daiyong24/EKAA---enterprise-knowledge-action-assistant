"""Request correlation and safe lifecycle telemetry middleware."""

from __future__ import annotations

import re
import uuid
from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.observability.metrics import MetricRegistry
from backend.app.observability.tracing import Tracer, current_trace_id

_SAFE_CORRELATION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class ObservabilityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        tracer: Tracer,
        metrics: MetricRegistry,
        api_prefix: str = "",
    ) -> None:
        self.app = app
        self._tracer = tracer
        self._metrics = metrics
        self._api_prefix = api_prefix.rstrip("/")

    def _safe_route(self, path: str) -> str:
        prefix = self._api_prefix
        fixed = {
            f"{prefix}/health": f"{prefix}/health",
            f"{prefix}/health/dependencies": f"{prefix}/health/dependencies",
            f"{prefix}/metrics": f"{prefix}/metrics",
            f"{prefix}/api/chat": f"{prefix}/api/chat",
            f"{prefix}/api/rag/answer": f"{prefix}/api/rag/answer",
            f"{prefix}/api/retrieval/search": f"{prefix}/api/retrieval/search",
            f"{prefix}/api/documents": f"{prefix}/api/documents",
            f"{prefix}/api/approvals": f"{prefix}/api/approvals",
        }
        if path in fixed:
            return fixed[path]
        approval_prefix = f"{prefix}/api/approvals/"
        if path.startswith(approval_prefix) and path[len(approval_prefix) :]:
            return f"{prefix}/api/approvals/{{request_id}}"
        admin_suffix = "/access"
        admin_prefix = f"{prefix}/api/admin/documents/"
        if path.startswith(admin_prefix) and path.endswith(admin_suffix):
            return f"{prefix}/api/admin/documents/{{document_id}}/access"
        return "unknown"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        raw = headers.get(b"x-correlation-id", b"").decode("ascii", "ignore")
        correlation = raw if _SAFE_CORRELATION.fullmatch(raw) else uuid.uuid4().hex
        status_code = 500
        started = perf_counter()
        method = scope.get("method", "unknown")
        safe_method = method if method in _SAFE_METHODS else "UNKNOWN"
        safe_route = self._safe_route(scope.get("path", ""))

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                existing = list(message.get("headers", []))
                existing.extend(
                    [
                        (b"x-correlation-id", correlation.encode("ascii")),
                        (
                            b"x-trace-id",
                            (current_trace_id() or correlation).encode("ascii"),
                        ),
                    ]
                )
                message = {**message, "headers": existing}
            await send(message)

        with self._tracer.span(
            f"http.{safe_method.lower()}",
            correlation_id=correlation,
            attributes={
                "http.method": safe_method,
                "http.route": safe_route,
            },
        ):
            try:
                await self.app(scope, receive, send_with_headers)
            finally:
                self._metrics.inc(
                    "ekaa_http_requests_total",
                    labels={
                        "method": safe_method,
                        "route": safe_route,
                        "status": str(status_code),
                    },
                )
                self._metrics.observe(
                    "ekaa_http_request_duration_ms",
                    (perf_counter() - started) * 1000,
                    labels={
                        "method": safe_method,
                        "route": safe_route,
                    },
                )
