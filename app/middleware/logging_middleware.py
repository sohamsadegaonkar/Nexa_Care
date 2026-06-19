"""Global request logging + last-resort exception handling.

Fix applied in this file:
  F-16 — The except branch referenced error_def.code, error_def.severity,
          and error_def.http_status. ErrorDefinition (see
          app/observability/error_catalog.py) actually exposes
          error_code, status_code, and retryable -- none of those three
          attribute names exist on it. Any unhandled exception reaching
          this middleware therefore raised AttributeError while trying to
          build the *fallback* error response, so the one safety net
          meant to catch "something went wrong and we don't know what"
          was itself broken: instead of a clean, audited 503, the
          request crashed with no structured log entry and no
          X-Trace-Id header at all. Since this branch only runs for
          genuinely unanticipated exceptions, there's no finer-grained
          severity to recover here -- it's always logged as critical.
"""
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

logger = logging.getLogger("nexa_logger")

class GlobalLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Setup Context (OpenTelemetry convention)
        trace_id = request.headers.get("X-Trace-Id") or generate_trace_id()
        request_id = str(uuid.uuid4())
        span_id = generate_span_id()

        trace_id_var.set(trace_id)
        request_id_var.set(request_id)
        span_id_var.set(span_id)

        # 2. Log Request Start
        logger.info(json.dumps({
            "event": "request_started",
            "trace_id": trace_id,
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path
        }))

        start_time = time.perf_counter()

        # 3. Execution & last-resort error handling
        try:
            response = await call_next(request)
        except Exception as e:
            # Fallback to DB_CONNECTION_LOST if unknown
            error_def = Catalog.DB_CONNECTION_LOST

            # F-16: error_def.error_code / .status_code are the real
            # field names on ErrorDefinition -- .code / .http_status
            # never existed. Anything reaching this branch is by
            # definition an unanticipated failure, so it's always
            # logged at CRITICAL rather than relying on a severity field
            # the dataclass doesn't have.
            log_msg = json.dumps({
                "event": "request_failed",
                "trace_id": trace_id,
                "request_id": request_id,
                "error_code": error_def.error_code,
                "exception": str(e)
            })
            logger.critical(log_msg)

            error_response = JSONResponse(
                status_code=error_def.status_code,
                content=redact_payload(get_error(error_def))
            )
            error_response.headers["X-Trace-Id"] = trace_id
            return error_response

        # 4. Log Success
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(json.dumps({
            "event": "request_completed",
            "trace_id": trace_id,
            "status": response.status_code,
            "latency_ms": latency
        }))

        response.headers["X-Trace-Id"] = trace_id
        return response