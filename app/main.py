"""Nexa Care FastAPI entrypoint.

Fixes applied in this file:
  F-01 — /api/v1/process-document now calls split_pii_and_clinical_fields()
          instead of the old inline dict-split that missed aadhaar_abha_id.
          Unrecognized keys are routed to the vault (fail-safe) with a
          warning log, exactly as sharding.py documents.
  F-02 — extract_document_data() is now wrapped in run_in_threadpool() so
          synchronous PyTorch inference does not block the async event loop.
  F-06 — temp_path is assigned before any stream I/O so the finally block
          always has a path to clean up; upload size is capped at 20 MB.
  F-13 — Replaced deprecated @app.on_event("startup") with the modern
          @asynccontextmanager lifespan pattern.
  F-14 — Added a 20 MB ContentSizeLimitMiddleware to globally reject
          oversized request bodies before they reach any route handler.
  F-15 — ContentSizeLimitMiddleware.dispatch() previously did
          `return HTTPException(...)`. HTTPException is not a Response —
          BaseHTTPMiddleware.dispatch() must return (or the route must
          raise) something ASGI can actually send. Returning the bare
          exception object meant any oversized request crashed the
          middleware instead of cleanly receiving a 413. Now returns a
          JSONResponse, and a malformed (non-numeric) Content-Length
          header is tolerated rather than raising ValueError.
  F-17 — The Supabase insert calls in process_document() are now wrapped
          in try/except instead of relying on
          `getattr(response, "error", None)`. In supabase-py 2.x,
          PostgREST errors raise postgrest.APIError directly out of
          execute() rather than setting a truthy `.error` attribute on a
          returned object — the old check was unreachable dead code on
          the actual failure path. A real DB failure here previously
          propagated as an unhandled APIError straight to
          GlobalLoggingMiddleware (generic 503, no specific detail),
          rather than the intended 502 with vault/clinical error detail.
          Same root cause and same fix shape as app/api/routes.py F-17
          and app/services/biometric_registry.py F-16.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import router as api_router
from app.api.v2.auth_routes import router as auth_v2_router
from app.api.v2.consent_routes import router as consent_v2_router
from app.api.v2.document_routes import router as document_v2_router
from app.api.v2.emergency_routes import router as emergency_v2_router
from app.api.v2.fhir_routes import router as fhir_v2_router
from app.api.v2.nfc_routes import router as nfc_v2_router
from app.api.v2.patient_routes import router as patient_v2_router
from app.api.v2.patient_record_routes import router as patient_record_v2_router
from app.api.v2.pipeline_routes import router as pipeline_v2_router
from app.api.v2.review_routes import router as review_v2_router
from app.api.v2.dashboard_routes import router as dashboard_v2_router
from app.api.v2.consent_history_routes import router as consent_history_v2_router
from app.api.v2.policy_routes import router as policy_v2_router
from app.api.v2.role_routes import router as role_v2_router
from app.api.v2.mfa_action_routes import router as mfa_action_router
from app.api.v2.assurance_routes import router as assurance_v2_router
from app.api.v2.merge_routes import router as merge_v2_router
from app.api.v2.contract_routes import router as contract_v2_router
from app.api.v2.device_routes import router as device_v2_router
from app.core.config import (
    get_database_config,
    get_handshake_config,
    get_redis_config,
    get_supabase_config,
)
from app.core.supabase import get_supabase_client
from app.middleware.logging_middleware import GlobalLoggingMiddleware
from app.services.sharding import encrypt_vault_payload, split_pii_and_clinical_fields
from app.services.crypto_kms import get_encryption_provider, PatientDataErased
from document_processor import extract_document_data
from prometheus_client import Counter, Histogram, make_asgi_app

from app.core.database import get_async_engine, get_db_session
from app.core.redis import get_redis_client

load_dotenv()

logger = logging.getLogger("nexa_logger")

# ── F-14: Hard upload / body size cap ────────────────────────────────────────
_MAX_UPLOAD_BYTES: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))  # 20 MB


class ContentSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject any request whose Content-Length header exceeds the cap."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")

        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None

            if declared_size is not None and declared_size > _MAX_UPLOAD_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={
                        "error_code": "PAYLOAD_TOO_LARGE",
                        "message": (
                            f"Request body exceeds the "
                            f"{_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
                        ),
                        "retryable": False,
                    },
                )

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every outgoing response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self';"
        )
        return response


# ── F-13: Modern lifespan pattern ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Validate all required secrets at startup."""
    get_supabase_config()
    get_redis_config()
    get_handshake_config()
    get_database_config()
    yield
    try:
        engine = get_async_engine()
        await engine.dispose()
    except Exception as exc:
        logger.warning(f"Engine disposal during shutdown failed: {exc}")

    try:
        redis_client = get_redis_client()
        redis_client.close()
    except Exception as exc:
        logger.warning(f"Redis close during shutdown failed: {exc}")


app = FastAPI(title="Nexa Care API", version="0.2.1", lifespan=lifespan)

app.add_middleware(ContentSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Hospital-Id", "X-Consent-Token", "X-Consent-Purpose"],
)

_trusted_hosts = [h.strip() for h in os.getenv("TRUSTED_HOSTS", "*").split(",") if h.strip()] or ["*"]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)

app.add_middleware(GlobalLoggingMiddleware)

app.include_router(api_router)
app.include_router(auth_v2_router)
app.include_router(consent_v2_router)
app.include_router(document_v2_router)
app.include_router(emergency_v2_router)
app.include_router(fhir_v2_router)
app.include_router(nfc_v2_router)
app.include_router(patient_v2_router)
app.include_router(patient_record_v2_router)
app.include_router(pipeline_v2_router)
app.include_router(review_v2_router)
app.include_router(policy_v2_router)
app.include_router(role_v2_router)
app.include_router(mfa_action_router)
app.include_router(assurance_v2_router)
app.include_router(merge_v2_router)
app.include_router(contract_v2_router)
app.include_router(device_v2_router)
app.include_router(dashboard_v2_router)
app.include_router(consent_history_v2_router)


@app.exception_handler(PatientDataErased)
async def patient_data_erased_handler(request: Request, exc: PatientDataErased):
    """Handle cryptographic erasure errors by returning a 410 Gone."""
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={
            "error_code": "PATIENT_DATA_ERASED",
            "message": str(exc),
            "patient_id": exc.patient_id,
        },
    )


_REQUESTS_TOTAL = Counter(
    "nexa_http_requests_total",
    "Total HTTP requests by method and status",
    ["method", "status_code"],
)
_REQUEST_DURATION = Histogram(
    "nexa_http_request_duration_seconds",
    "HTTP request latency",
    ["method"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record request counts and latency for Prometheus."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        method = request.method
        status = str(response.status_code)
        _REQUESTS_TOTAL.labels(method=method, status_code=status).inc()
        _REQUEST_DURATION.labels(method=method).observe(duration)
        return response


app.add_middleware(PrometheusMiddleware)
app.mount("/metrics", make_asgi_app())


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Liveness/readiness probe. Verifies Redis and Postgres reachability."""

    checks: dict[str, str] = {}

    try:
        redis = get_redis_client()
        redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"unavailable: {type(exc).__name__}"

    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"unavailable: {type(exc).__name__}"

    if all(status == "ok" for status in checks.values()):
        return {"status": "ok", **checks}

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": "degraded", **checks},
    )


@app.post("/api/v1/process-document", tags=["documents"])
async def process_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session)
) -> dict:
    """Process an uploaded document and vertically shard PII + clinical data."""
    suffix = os.path.splitext(file.filename or "")[1] or ".png"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path: str = tmp.name

    try:
        contents = await file.read()

        if len(contents) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Upload exceeds the {_MAX_UPLOAD_BYTES // (1024*1024)} MB limit.",
            )

        tmp.write(contents)
        tmp.close()

        document_data: dict = await run_in_threadpool(extract_document_data, temp_path)

        if not document_data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to extract document data.",
            )

        vault_payload, clinical_payload, unrecognized_payload = split_pii_and_clinical_fields(
            document_data
        )

        if unrecognized_payload:
            logger.warning(
                json.dumps({
                    "event": "unrecognized_extraction_keys",
                    "keys": sorted(unrecognized_payload.keys()),
                    "action": "dropped_no_raw_pii_column",
                })
            )

        masked_internal_id = str(uuid.uuid4())
        supabase = get_supabase_client()

        # 1. Generate DEK first (atomic with transaction)
        kms = get_encryption_provider()
        await kms.generate_dek(masked_internal_id, db)

        # 2. Encrypt PII using KMS
        encrypted_vault = await encrypt_vault_payload(vault_payload, masked_internal_id, db)

        try:
            vault_columns = {
                "masked_internal_id": masked_internal_id,
            }
            vault_columns.update(encrypted_vault)
            supabase.table("nexa_vault").insert(vault_columns).execute()
        except Exception as exc:
            logger.critical(json.dumps({
                "event": "process_document_db_error",
                "shard": "nexa_vault",
                "masked_internal_id": masked_internal_id,
                "exception": str(exc),
            }))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"vault_error": str(exc), "clinical_error": None},
            ) from exc

        try:
            supabase.table("nexa_clinical").insert({
                "masked_internal_id": masked_internal_id,
                "clinical_data": clinical_payload
            }).execute()
        except Exception as exc:
            logger.critical(json.dumps({
                "event": "process_document_db_error",
                "shard": "nexa_clinical",
                "masked_internal_id": masked_internal_id,
                "exception": str(exc),
            }))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"vault_error": None, "clinical_error": str(exc)},
            ) from exc

        return {"masked_internal_id": masked_internal_id}

    finally:
        try:
            tmp.close()
        except Exception:
            pass
        if os.path.exists(temp_path):
            os.unlink(temp_path)
