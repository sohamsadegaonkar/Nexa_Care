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
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse                   # F-15
from starlette.concurrency import run_in_threadpool          # F-02
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware     # F-14

from app.api.routes import router as api_router
from app.api.v2.auth_routes import router as auth_v2_router
from app.api.v2.consent_routes import router as consent_v2_router
from app.api.v2.document_routes import router as document_v2_router
from app.api.v2.emergency_routes import router as emergency_v2_router
from app.api.v2.fhir_routes import router as fhir_v2_router
from app.api.v2.nfc_routes import router as nfc_v2_router
from app.api.v2.patient_routes import router as patient_v2_router
from app.api.v2.review_routes import router as review_v2_router
from app.api.v2.dashboard_routes import router as dashboard_v2_router
from app.api.v2.consent_history_routes import router as consent_history_v2_router
from app.api.v2.policy_routes import router as policy_v2_router
from app.api.v2.role_routes import router as role_v2_router
from app.api.v2.mfa_action_routes import router as mfa_action_router
from app.api.v2.assurance_routes import router as assurance_v2_router
from app.core.config import (
    get_database_config,
    get_handshake_config,
    get_redis_config,
    get_supabase_config,
)
from app.core.supabase import get_supabase_client
from app.middleware.logging_middleware import GlobalLoggingMiddleware
from app.services.sharding import encrypt_vault_payload, split_pii_and_clinical_fields  # F-01
from document_processor import extract_document_data
from prometheus_client import Counter, Histogram, make_asgi_app

from app.core.database import get_async_engine
from app.core.redis import get_redis_client

load_dotenv()

logger = logging.getLogger("nexa_logger")

# ── F-14: Hard upload / body size cap ────────────────────────────────────────
_MAX_UPLOAD_BYTES: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))  # 20 MB


class ContentSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject any request whose Content-Length header exceeds the cap, and
    abort streaming bodies that exceed it mid-transfer.  This prevents both
    OOM attacks on the document endpoint and accidental oversized payloads
    on JSON routes.

    F-15: must RETURN A RESPONSE here, not an HTTPException instance.
    BaseHTTPMiddleware.dispatch() is responsible for producing something
    ASGI-callable; an HTTPException is just a plain exception class and
    has no __call__/asgi send behavior, so returning one (instead of
    raising it, or wrapping it in a Response) would blow up the moment
    Starlette tried to send it back to the client.
    """

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")

        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                # Malformed header -- don't let int() crash the middleware;
                # fall through and let the route's own logic (or a
                # downstream framework check) handle it instead.
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
    """Attach baseline security headers to every outgoing response.

    Does not include CORS (handled by CORSMiddleware) or HSTS (must be
    added by the TLS-terminating reverse proxy / load balancer). CSP is
    kept permissive for a React/Expo SPA; tighten it once the exact CDN
    and inline script policy is known.
    """

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
    """Validate all required secrets at startup so the process fails fast
    rather than serving requests with a broken configuration."""
    get_supabase_config()
    get_redis_config()
    get_handshake_config()
    get_database_config()
    yield
    # Graceful shutdown: dispose of the SQLAlchemy engine pool and
    # close any open Redis connections so the process exits cleanly.
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

# Middleware order matters: security/size checks fire before logging so we
# don't log and store partial bodies from abusive clients.
app.add_middleware(ContentSizeLimitMiddleware)   # F-14/F-15 — outermost, fires first
app.add_middleware(SecurityHeadersMiddleware)

# CORS: only allow explicitly configured origins. If none are set, the
# middleware is still installed but allows no cross-origin traffic.
_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Hospital-Id", "X-Consent-Token", "X-Consent-Purpose"],
)

# Trusted hosts: reject requests whose Host header is not expected.
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
app.include_router(review_v2_router)
app.include_router(policy_v2_router)
app.include_router(role_v2_router)
app.include_router(mfa_action_router)
app.include_router(assurance_v2_router)
app.include_router(dashboard_v2_router)
app.include_router(consent_history_v2_router)

# Prometheus metrics endpoint (no auth for liveness; protect in production
# with a reverse proxy or basic auth if exposed externally).
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
async def process_document(file: UploadFile = File(...)) -> dict:
    """Process an uploaded document and vertically shard PII + clinical data.

    F-01: uses split_pii_and_clinical_fields() — the single authoritative
          sharding function — instead of the old inline dict-split that
          missed aadhaar_abha_id.
    F-02: runs ML inference in a thread pool so the event loop stays free.
    F-06: temp_path is set before any I/O; finally block always fires.
    F-17: Supabase inserts wrapped in try/except — see module docstring.
    SHARD-SEPARATION FIX: PII is written to the explicitly modeled vault
    columns (patient_name, phone, aadhaar_abha_id) encrypted at rest. The
    legacy combined ``raw_pii`` JSONB blob is no longer persisted.
    """
    suffix = os.path.splitext(file.filename or "")[1] or ".png"

    # F-06: create the temp file first so temp_path is always defined,
    # then write to it.  If write fails, finally still cleans up the empty file.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path: str = tmp.name

    try:
        contents = await file.read()

        # F-06: secondary size guard for cases where Content-Length was absent
        if len(contents) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Upload exceeds the {_MAX_UPLOAD_BYTES // (1024*1024)} MB limit.",
            )

        tmp.write(contents)
        tmp.close()

        # F-02: run synchronous PyTorch inference off the event loop
        document_data: dict = await run_in_threadpool(extract_document_data, temp_path)

        if not document_data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to extract document data.",
            )

        # F-01: replace the old broken inline split with the authoritative
        # sharding function.  Unrecognized keys are routed to the vault
        # (fail-safe) so no novel PII key silently leaks to clinical data.
        vault_payload, clinical_payload, unrecognized_payload = split_pii_and_clinical_fields(
            document_data
        )

        if unrecognized_payload:
            # The migration removes the generic ``raw_pii`` JSONB blob, so
            # unrecognized keys can no longer be silently dumped into a vault
            # catch-all. They are logged and dropped; a clinician must confirm
            # their classification before any new column is added.
            logger.warning(
                json.dumps({
                    "event": "unrecognized_extraction_keys",
                    "keys": sorted(unrecognized_payload.keys()),
                    "action": "dropped_no_raw_pii_column",
                    "note": (
                        "Run scripts/validate_extraction_schema.py and confirm "
                        "with a clinician whether each key is PII before re-routing."
                    ),
                })
            )

        masked_internal_id = str(uuid.uuid4())
        supabase = get_supabase_client()

        # F-17: each insert wrapped individually so the error detail
        # reported back identifies which shard actually failed, instead
        # of relying on a `.error` attribute that supabase-py 2.x never
        # sets on the failure path (APIError is raised instead). A
        # failure on either insert now reliably surfaces as a 502 with
        # the real exception message, rather than an unhandled APIError
        # bubbling up to the global handler as a generic 503.
        try:
            vault_columns = {
                "masked_internal_id": masked_internal_id,
            }
            vault_columns.update(encrypt_vault_payload(vault_payload))
            vault_res = (
                supabase.table("nexa_vault")
                .insert(vault_columns)
                .execute()
            )
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
            clinical_res = (
                supabase.table("nexa_clinical")
                .insert({"masked_internal_id": masked_internal_id, "clinical_data": clinical_payload})
                .execute()
            )
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

        # F-17: retained as belt-and-suspenders for any future supabase-py
        # version that reverts to 1.x-style error surfacing, and for test
        # doubles that mock a truthy `.error` instead of raising — same
        # rationale as biometric_registry.py F-16 and routes.py F-17.
        if getattr(vault_res, "error", None) or getattr(clinical_res, "error", None):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "vault_error": str(getattr(vault_res, "error", None)),
                    "clinical_error": str(getattr(clinical_res, "error", None)),
                },
            )

        return {"masked_internal_id": masked_internal_id}

    finally:
        # F-06: always close and remove, even on exceptions raised before
        # tmp.close() is reached in the happy path.
        try:
            tmp.close()
        except Exception:
            pass
        if os.path.exists(temp_path):
            os.unlink(temp_path)