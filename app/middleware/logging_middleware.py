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

        # 3. Execution & Dynamic Error Handling
        try:
            response = await call_next(request)
        except Exception as e:
            # Fallback to DB_CONNECTION_LOST if unknown
            error_def = Catalog.DB_CONNECTION_LOST
            
            # Log with catalog severity
            log_msg = json.dumps({
                "event": "request_failed",
                "trace_id": trace_id,
                "request_id": request_id,
                "error_code": error_def.code,
                "exception": str(e)
            })
            
            if error_def.severity == "CRITICAL":
                logger.critical(log_msg)
            else:
                logger.error(log_msg)

            return JSONResponse(
                status_code=error_def.http_status,
                content=redact_payload(get_error(error_def))
            )

        # 4. Log Success
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(json.dumps({
            "event": "request_completed",
            "trace_id": trace_id,
            "status": response.status_code,
            "latency_ms": latency
        }))
        
        return response