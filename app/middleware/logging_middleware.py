import time
import json
import logging
import uuid
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.request_context import (
    trace_id_var, request_id_var, span_id_var,
    generate_trace_id, generate_span_id
)
from app.observability.redactor import redact_payload
from app.observability.error_catalog import Catalog, get_error
from app.observability.safe_exceptions import log_safe_exception, safe_error_response

logger = logging.getLogger("nexa_logger")


def _classify_exception(exc: Exception):
    """Map an unhandled exception to the appropriate ErrorDefinition.

    Fixes applied:
      B-06 — the old code always used Catalog.DB_CONNECTION_LOST regardless
              of the actual exception type.  A Pydantic ValidationError, a
              Redis timeout, and a PostgREST APIError all returned an
              identical 503 DB_CONNECTION_LOST, making it impossible to
              distinguish classes of failure in alerting or dashboards.

      B-07 — uses type(e).__name__ string matching rather than a direct
              import of postgrest.exceptions.APIError or
              fastapi.exceptions.RequestValidationError, so the middleware
              has no hard dependency on those packages' internal import
              paths (which have shifted across minor versions).

    Classification:
      APIError            PostgREST / Supabase DB error   -> DB_CONNECTION_LOST (503)
      RequestValidationError / ValidationError             -> VALIDATION_ERROR   (400)
      Everything else                                      -> INTERNAL_SERVER_ERROR (500)
    """
    exc_type = type(exc).__name__
    if exc_type == "APIError":
        # PostgREST surfaced an error (bad gateway to the DB layer).
        # DB_CONNECTION_LOST is the closest catalog entry; status_code=503.
        return Catalog.DB_CONNECTION_LOST
    if exc_type in ("RequestValidationError", "ValidationError"):
        return Catalog.VALIDATION_ERROR
    return Catalog.INTERNAL_SERVER_ERROR


class GlobalLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Setup context (OpenTelemetry convention)
        trace_id = request.headers.get("X-Trace-Id") or generate_trace_id()
        request_id = str(uuid.uuid4())
        span_id = generate_span_id()

        trace_id_var.set(trace_id)
        request_id_var.set(request_id)
        span_id_var.set(span_id)

        # 2. Log request start
        logger.info(json.dumps({
            "event": "request_started",
            "trace_id": trace_id,
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        }))

        start_time = time.perf_counter()

        # 3. Execute route; catch any exception that escaped route-level handling
        try:
            response = await call_next(request)

        except Exception as exc:
            # B-06 fix: classify the exception before touching any catalog fields.
            error_def = _classify_exception(exc)

            # B-07 fix: ErrorDefinition fields are .error_code and .status_code.
            # The old code used .code and .http_status (both AttributeError),
            # which caused the exception handler itself to crash and return a
            # blank 500 with no body or trace-id header.
            safe_error = log_safe_exception(
                logger,
                logging.ERROR if error_def.status_code >= 500 else logging.WARNING,
                "request_failed",
                exc,
                subsystem="http",
                operation=f"{request.method} {request.url.path}",
                fields={"trace_id": trace_id, "request_id": request_id},
            )

            error_response = JSONResponse(
                status_code=error_def.status_code,    # was: error_def.http_status (AttributeError)
                content=safe_error_response(safe_error, error_def.message),
            )
            error_response.headers["X-Trace-Id"] = trace_id
            error_response.headers["X-Error-Id"] = str(safe_error["error_id"])
            return error_response

        # 4. Log success
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(json.dumps({
            "event": "request_completed",
            "trace_id": trace_id,
            "request_id": request_id,
            "status": response.status_code,
            "latency_ms": latency,
        }))

        response.headers["X-Trace-Id"] = trace_id
        return response
