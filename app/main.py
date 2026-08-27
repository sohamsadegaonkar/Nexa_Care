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

import asyncio
import logging
import os
import time
import secrets
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
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
from app.api.v2.identity_review_routes import identity_review_v2_router
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
from app.api.v2.patient_self_routes import router as patient_self_v2_router
from app.core.config import (
    get_database_config,
    get_handshake_config,
    get_redis_config,
    get_supabase_config,
    get_document_extraction_config,
    get_document_storage_config,
    get_runtime_environment,
)
from app.middleware.logging_middleware import GlobalLoggingMiddleware
from app.services.crypto_kms import get_encryption_provider, PatientDataErased
from app.security.erasure_registry import ErasureRegistryUnavailable
from prometheus_client import Counter, Histogram, make_asgi_app

from app.core.database import get_async_engine, get_db_session, get_session_factory
from app.core.redis import get_async_redis_client
from app.core.client_ip import trusted_proxy_networks
from app.observability.safe_exceptions import log_safe_exception
from app.services.audit_outbox_processor import (
    get_outbox_health,
    run_outbox_processor_forever,
)
from app.services.failure_quarantine_processor import (
    run_failure_quarantine_processor_forever,
)
from app.ai.async_textract import AsyncTextractProvider
from app.services.provider_job_reconciliation_processor import (
    run_provider_job_reconciliation_processor_forever,
)
from app.services.textract_async_runtime import make_textract_reconciliation_callback
from app.services.textract_source_staging import (
    TextractSourceStager,
    TextractStagingConfig,
)
from app.services.document_storage import get_document_storage

# Deprecated test-patch seam; runtime code uses get_async_redis_client.
get_redis_client = get_async_redis_client

load_dotenv()

logger = logging.getLogger("nexa_logger")

# ── F-14: Hard upload / body size cap ────────────────────────────────────────
_MAX_UPLOAD_BYTES: int = int(
    os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
)  # 20 MB


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
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none';"
        )
        if get_runtime_environment().is_production_like:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


class CookieCsrfMiddleware(BaseHTTPMiddleware):
    """Origin + double-submit protection for provider cookie sessions."""

    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    _LOGIN_EXEMPT = {"/api/v2/auth/web/login", "/api/v2/auth/web/mfa/verify"}

    async def dispatch(self, request: Request, call_next):
        cookie_session = request.cookies.get("nexa_provider_session")
        if (
            cookie_session
            and request.method not in self._SAFE_METHODS
            and request.url.path not in self._LOGIN_EXEMPT
        ):
            origin = request.headers.get("origin")
            configured = {
                o.strip().rstrip("/")
                for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
                if o.strip()
            }
            same_origin = (
                f"{request.url.scheme}://{request.headers.get('host', '')}".rstrip("/")
            )
            if not origin or origin.rstrip("/") not in configured | {same_origin}:
                return JSONResponse(
                    status_code=403, content={"error_code": "CSRF_ORIGIN_REJECTED"}
                )
            cookie_token = request.cookies.get("nexa_csrf", "")
            header_token = request.headers.get("x-csrf-token", "")
            if (
                not cookie_token
                or not header_token
                or not secrets.compare_digest(cookie_token, header_token)
            ):
                return JSONResponse(
                    status_code=403, content={"error_code": "CSRF_TOKEN_REJECTED"}
                )
        return await call_next(request)


# ── F-13: Modern lifespan pattern ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Validate all required secrets at startup."""
    get_supabase_config()
    get_redis_config()
    get_handshake_config()
    get_database_config()
    get_document_extraction_config()
    get_document_storage_config()
    get_encryption_provider()
    trusted_proxy_networks()
    outbox_shutdown_event = asyncio.Event()
    outbox_task = asyncio.create_task(
        run_outbox_processor_forever(
            get_session_factory(),
            shutdown_event=outbox_shutdown_event,
        )
    )
    application.state.audit_outbox_task = outbox_task
    failure_quarantine_shutdown_event = asyncio.Event()
    failure_quarantine_task = asyncio.create_task(
        run_failure_quarantine_processor_forever(
            get_session_factory(), shutdown_event=failure_quarantine_shutdown_event
        )
    )
    application.state.failure_quarantine_task = failure_quarantine_task
    provider_reconciliation_shutdown_event = None
    provider_reconciliation_task = None
    extraction_config = get_document_extraction_config()
    storage_config = get_document_storage_config()
    if extraction_config.async_multipage_enabled and storage_config.provider == "s3":
        storage = get_document_storage()
        stager = TextractSourceStager(
            config=TextractStagingConfig(
                bucket=storage_config.s3_bucket or "",
                region=storage_config.s3_region or extraction_config.aws_region,
                kms_key_id=storage_config.s3_kms_key_id or "",
            ),
            storage=storage,
            s3_client=getattr(storage, "client", None),
            io_timeout_seconds=extraction_config.timeout_seconds,
        )
        provider = AsyncTextractProvider(
            region=extraction_config.aws_region,
            timeout_seconds=extraction_config.timeout_seconds,
        )
        provider_reconciliation_shutdown_event = asyncio.Event()
        provider_reconciliation_task = asyncio.create_task(
            run_provider_job_reconciliation_processor_forever(
                get_session_factory(),
                reconcile_callback=make_textract_reconciliation_callback(
                    session_factory=get_session_factory(),
                    provider=provider,
                    stager=stager,
                ),
                max_attempts=extraction_config.reconciliation_max_attempts,
                window_seconds=extraction_config.reconciliation_window_seconds,
                poll_interval_seconds=extraction_config.reconciliation_interval_seconds,
                batch_size=extraction_config.reconciliation_batch_size,
                shutdown_event=provider_reconciliation_shutdown_event,
            )
        )
    application.state.provider_reconciliation_task = provider_reconciliation_task
    try:
        yield
    finally:
        if provider_reconciliation_shutdown_event is not None:
            provider_reconciliation_shutdown_event.set()
        if provider_reconciliation_task is not None:
            await provider_reconciliation_task
        application.state.provider_reconciliation_task = None
        failure_quarantine_shutdown_event.set()
        await failure_quarantine_task
        application.state.failure_quarantine_task = None
        outbox_shutdown_event.set()
        await outbox_task
        application.state.audit_outbox_task = None
    try:
        engine = get_async_engine()
        await engine.dispose()
    except Exception as exc:
        log_safe_exception(
            logger, exc, subsystem="database", operation="shutdown_dispose"
        )

    try:
        redis_client = get_async_redis_client()
        await redis_client.close()
    except Exception as exc:
        log_safe_exception(logger, exc, subsystem="redis", operation="shutdown_close")


app = FastAPI(title="Nexa Care API", version="0.2.1", lifespan=lifespan)

app.add_middleware(ContentSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CookieCsrfMiddleware)

_cors_origins = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Hospital-Id",
        "X-Consent-Token",
        "X-Consent-Purpose",
        "X-CSRF-Token",
        "Idempotency-Key",
    ],
)

_trusted_hosts = [
    h.strip() for h in os.getenv("TRUSTED_HOSTS", "*").split(",") if h.strip()
] or ["*"]
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
app.include_router(identity_review_v2_router)
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
app.include_router(patient_self_v2_router)


@app.exception_handler(PatientDataErased)
async def patient_data_erased_handler(request: Request, exc: PatientDataErased):
    """Handle cryptographic erasure errors by returning a 410 Gone."""
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={
            "error_code": "PATIENT_DATA_ERASED",
            "message": "Patient encrypted data is no longer available.",
            "patient_id": exc.patient_id,
        },
    )


@app.exception_handler(ErasureRegistryUnavailable)
async def erasure_registry_unavailable_handler(
    request: Request, exc: ErasureRegistryUnavailable
):
    """A registry query failure is never treated as 'not erased' -- fail
    closed with a 503 rather than silently permitting decryption."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error_code": "ERASURE_REGISTRY_UNAVAILABLE",
            "message": "Could not verify erasure status; access denied.",
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


@app.get("/healthz", tags=["health"])
async def liveness_check() -> dict:
    """Dependency-free liveness probe for deployment platforms."""

    return {"status": "ok"}


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Readiness probe. Verifies Redis and Postgres reachability."""

    checks: dict[str, str] = {}
    outbox_task = getattr(app.state, "audit_outbox_task", None)
    checks["audit_outbox_worker"] = (
        "ok" if outbox_task is not None and not outbox_task.done() else "unavailable"
    )

    try:
        redis = get_async_redis_client()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"unavailable: {type(exc).__name__}"

    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
        async with get_session_factory()() as db:
            outbox_health = await get_outbox_health(db)
        checks["audit_outbox_pending_count"] = str(outbox_health["pending_count"])
        checks["audit_outbox_dead_letter_backlog"] = str(
            outbox_health["dead_letter_backlog"]
        )
        checks["audit_outbox_expired_lease_count"] = str(
            outbox_health["expired_lease_count"]
        )
        checks["audit_outbox_oldest_pending_age_seconds"] = str(
            round(outbox_health["oldest_pending_age_seconds"], 3)
        )
        checks["audit_outbox_oldest_expired_lease_age_seconds"] = str(
            round(outbox_health["oldest_expired_lease_age_seconds"], 3)
        )
        dead_letter_limit = int(os.getenv("AUDIT_OUTBOX_MAX_DEAD_LETTERS", "0"))
        expired_lease_limit = int(os.getenv("AUDIT_OUTBOX_MAX_EXPIRED_LEASES", "0"))
        oldest_pending_limit = int(
            os.getenv("AUDIT_OUTBOX_MAX_PENDING_AGE_SECONDS", "300")
        )
        if (
            outbox_health["dead_letter_backlog"] > dead_letter_limit
            or outbox_health["expired_lease_count"] > expired_lease_limit
            or outbox_health["oldest_pending_age_seconds"] > oldest_pending_limit
        ):
            checks["audit_outbox_backlog"] = "unhealthy"
        else:
            checks["audit_outbox_backlog"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"unavailable: {type(exc).__name__}"

    readiness_checks = (
        "audit_outbox_worker",
        "redis",
        "postgres",
        "audit_outbox_backlog",
    )
    if all(checks.get(name) == "ok" for name in readiness_checks):
        return {"status": "ok", **checks}

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": "degraded", **checks},
    )


@app.post("/api/v1/process-document", tags=["documents"])
async def process_document(
    file: UploadFile = File(...), db: AsyncSession = Depends(get_db_session)
) -> dict:
    """Retired legacy ingestion path; use the reviewed v2 pipeline."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "LEGACY_DOCUMENT_PIPELINE_RETIRED",
            "message": "Use the authenticated v2 upload, review, and commit workflow.",
        },
    )
