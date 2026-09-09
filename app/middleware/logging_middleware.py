import json
import logging
import re
import time
import uuid

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.request_context import (
    generate_span_id,
    generate_trace_id,
    request_id_var,
    span_id_var,
    trace_id_var,
)
from app.observability.error_catalog import Catalog
from app.observability.safe_exceptions import log_safe_exception, safe_error_response

logger = logging.getLogger("nexa_logger")
_TRACE_ID_RE = re.compile(r"^(?:trace-)?[0-9a-fA-F]{32}$")


def _classify_exception(exc: Exception):
    """Map an unhandled exception to the appropriate ErrorDefinition."""
    exc_type = type(exc).__name__
    if exc_type == "APIError":
        return Catalog.DB_CONNECTION_LOST
    if exc_type in ("RequestValidationError", "ValidationError"):
        return Catalog.VALIDATION_ERROR
    return Catalog.INTERNAL_SERVER_ERROR


def _safe_trace_id(request: Request) -> str:
    supplied = request.headers.get("X-Trace-Id", "").strip()
    if supplied and _TRACE_ID_RE.fullmatch(supplied):
        return supplied.lower()
    return generate_trace_id()


def _route_template(request: Request) -> str:
    """Return only the server-owned route template, never raw URL values."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/") and len(template) <= 192:
        return template
    return "unresolved"


class GlobalLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = _safe_trace_id(request)
        request_id = str(uuid.uuid4())
        span_id = generate_span_id()

        trace_id_var.set(trace_id)
        request_id_var.set(request_id)
        span_id_var.set(span_id)

        logger.info(
            json.dumps(
                {
                    "event": "request_started",
                    "trace_id": trace_id,
                    "request_id": request_id,
                    "method": request.method,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            error_def = _classify_exception(exc)
            route = _route_template(request)
            safe_error = log_safe_exception(
                logger,
                logging.ERROR if error_def.status_code >= 500 else logging.WARNING,
                "request_failed",
                exc,
                subsystem="http",
                operation=f"{request.method} {route}",
                fields={
                    "trace_id": trace_id,
                    "request_id": request_id,
                    "route": route,
                },
            )
            error_response = JSONResponse(
                status_code=error_def.status_code,
                content=safe_error_response(safe_error, error_def.message),
            )
            error_response.headers["X-Trace-Id"] = trace_id
            error_response.headers["X-Error-Id"] = str(safe_error["error_id"])
            return error_response

        latency = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            json.dumps(
                {
                    "event": "request_completed",
                    "trace_id": trace_id,
                    "request_id": request_id,
                    "route": _route_template(request),
                    "status": response.status_code,
                    "latency_ms": latency,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )

        response.headers["X-Trace-Id"] = trace_id
        return response
