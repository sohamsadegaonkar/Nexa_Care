import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability.error_catalog import Catalog, get_error
from app.observability.redactor import redact_payload
from app.core.request_context import trace_id_var, span_id_var

logger = logging.getLogger(__name__)

class GlobalLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        trace_id = trace_id_var.get()
        span_id = span_id_var.get()

        # [FINDING #16 FIX]: Include both trace_id and span_id in the start log
        logger.info(f"[Trace: {trace_id}] [Span: {span_id}] Request Started: {request.method} {request.url.path}")

        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            
            # [FINDING #16 FIX]: Include both IDs in the completion log so requests can be fully traced
            logger.info(f"[Trace: {trace_id}] [Span: {span_id}] Request Completed: {request.method} {request.url.path} - Status: {response.status_code} - Time: {process_time:.3f}s")
            
            return response

        except Exception as e:
            process_time = time.time() - start_time
            
            # [CRITICAL LOGGING FIX]: Always log the REAL exception trace so developers can debug it!
            logger.critical(
                f"[Trace: {trace_id}] [Span: {span_id}] Unhandled Exception: {request.method} {request.url.path} "
                f"- Error: {str(e)} - Time: {process_time:.3f}s", 
                exc_info=True
            )

            # [FINDINGS #7 & #12 FIX]: Differentiate exception types instead of blindly returning 503 DB_CONNECTION_LOST
            if isinstance(e, StarletteHTTPException):
                # If it's a known FastAPI/Starlette HTTP exception, let it pass through normally
                return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
            
            error_name = e.__class__.__name__
            
            # Map specific Python exceptions to our specific Business Error Catalog
            if error_name == "TimeoutError":
                error_def = Catalog.DOC_TIMEOUT
            elif error_name in ["PermissionError", "AuthenticationError", "JWTError"]:
                error_def = Catalog.AUTH_INVALID
            elif error_name in ["OperationalError", "InterfaceError", "DatabaseError", "RedisError"]:
                error_def = Catalog.DB_CONNECTION_LOST
            elif error_name in ["ValueError", "ValidationError"]:
                error_def = Catalog.VALIDATION_ERROR
            else:
                # Catch-all for genuinely unexpected application bugs
                error_def = Catalog.INTERNAL_SERVER_ERROR

            error_payload = get_error(error_def)
            redacted_payload = redact_payload(error_payload)

            return JSONResponse(
                status_code=error_def.status_code,
                content=redacted_payload
            )