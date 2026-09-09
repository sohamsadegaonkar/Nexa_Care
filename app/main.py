"""Nexa Care FastAPI entrypoint and production runtime boundary."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.ai.async_textract import AsyncTextractProvider
from app.api.routes import router as api_router
from app.api.v2.assurance_routes import router as assurance_v2_router
from app.api.v2.auth_routes import router as auth_v2_router
from app.api.v2.consent_history_routes import router as consent_history_v2_router
from app.api.v2.consent_routes import router as consent_v2_router
from app.api.v2.consent_v3_routes import router as consent_v3_router
from app.api.v2.contract_routes import router as contract_v2_router
from app.api.v2.dashboard_routes import router as dashboard_v2_router
from app.api.v2.device_routes import router as device_v2_router
from app.api.v2.document_routes import router as document_v2_router
from app.api.v2.emergency_routes import router as emergency_v2_router
from app.api.v2.fhir_routes import router as fhir_v2_router
from app.api.v2.identity_review_routes import identity_review_v2_router
from app.api.v2.merge_routes import router as merge_v2_router
from app.api.v2.mfa_action_routes import router as mfa_action_router
from app.api.v2.nfc_routes import router as nfc_v2_router
from app.api.v2.patient_discovery_routes import router as patient_discovery_v2_router
from app.api.v2.patient_record_routes import router as patient_record_v2_router
from app.api.v2.patient_routes import router as patient_v2_router
from app.api.v2.patient_self_routes import router as patient_self_v2_router
from app.api.v2.pipeline_routes import router as pipeline_v2_router
from app.api.v2.policy_routes import router as policy_v2_router
from app.api.v2.provider_trust_permission_routes import (
    ProviderTrustPermissionRouteError,
    provider_trust_permission_route_error_response,
    router as provider_trust_permission_v2_router,
)
from app.api.v2.provider_trust_routes import (
    ProviderTrustRouteError,
    provider_trust_route_error_response,
    router as provider_trust_v2_router,
)
from app.api.v2.review_routes import router as review_v2_router
from app.api.v2.role_routes import router as role_v2_router
from app.core.client_ip import trusted_proxy_networks
from app.core.config import (
    get_database_config,
    get_document_extraction_config,
    get_document_storage_config,
    get_handshake_config,
    get_redis_config,
    get_runtime_environment,
    get_supabase_config,
)
from app.core.database import get_async_engine, get_db_session, get_session_factory
from app.core.production_runtime import (
    MAX_UPLOAD_BYTES_HARD_LIMIT,
    RuntimePreflightReport,
    get_operations_auth_token,
    run_production_startup_preflight,
    verify_aws_runtime,
)
from app.core.redis import get_async_redis_client
from app.middleware.logging_middleware import GlobalLoggingMiddleware
from app.observability.safe_exceptions import log_safe_exception
from app.security.erasure_registry import ErasureRegistryUnavailable
from app.services.audit_outbox_processor import (
    get_outbox_health,
    run_outbox_processor_forever,
)
from app.services.crypto_kms import PatientDataErased, get_encryption_provider
from app.services.document_storage import get_document_storage
from app.services.failure_quarantine_processor import (
    run_failure_quarantine_processor_forever,
)
from app.services.provider_job_reconciliation_processor import (
    run_provider_job_reconciliation_processor_forever,
)
from app.services.textract_async_runtime import make_textract_reconciliation_callback
from app.services.textract_source_staging import TextractSourceStager, TextractStagingConfig

# Deprecated test-patch seam; runtime code uses get_async_redis_client.
get_redis_client = get_async_redis_client

load_dotenv()
logger = logging.getLogger("nexa_logger")


def _configured_upload_limit() -> int:
    raw = os.environ.get("MAX_UPLOAD_BYTES", str(MAX_UPLOAD_BYTES_HARD_LIMIT))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("MAX_UPLOAD_BYTES_INVALID") from exc
    if not 1 <= value <= MAX_UPLOAD_BYTES_HARD_LIMIT:
        raise RuntimeError("MAX_UPLOAD_BYTES_INVALID")
    return value


_MAX_UPLOAD_BYTES = _configured_upload_limit()


class ContentSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject declared request bodies above the hard application cap."""

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
                        "message": "Request body exceeds the configured size limit.",
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
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none';"
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
                item.strip().rstrip("/")
                for item in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
                if item.strip()
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


async def _supervise_worker(name: str, worker_factory, shutdown_event: asyncio.Event) -> None:
    """Restart a long-running worker after an unexpected top-level exit."""

    backoff_seconds = 1.0
    while not shutdown_event.is_set():
        try:
            await worker_factory()
            if shutdown_event.is_set():
                return
            raise RuntimeError("BACKGROUND_WORKER_EXITED_UNEXPECTEDLY")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_safe_exception(
                logger,
                logging.ERROR,
                "background_worker_restart",
                exc,
                subsystem="background_worker",
                operation="supervise",
                fields={"worker": name},
            )
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=backoff_seconds)
        except asyncio.TimeoutError:
            pass
        backoff_seconds = min(backoff_seconds * 2, 30.0)


async def _stop_worker(
    name: str, task: asyncio.Task | None, shutdown_event: asyncio.Event | None
) -> None:
    if shutdown_event is not None:
        shutdown_event.set()
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=15)
    except asyncio.TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        logger.warning("background_worker_forced_cancel", extra={"worker": name})
    except Exception as exc:
        log_safe_exception(
            logger,
            logging.ERROR,
            "background_worker_shutdown_failed",
            exc,
            subsystem="background_worker",
            operation="shutdown",
            fields={"worker": name},
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Fail closed before traffic, then supervise all long-running workers."""

    get_supabase_config()
    get_redis_config()
    get_handshake_config()
    get_database_config()
    extraction_config = get_document_extraction_config()
    storage_config = get_document_storage_config()
    get_encryption_provider()
    trusted_proxy_networks()

    preflight = await run_production_startup_preflight()
    application.state.runtime_preflight_report = preflight

    provider_worker_factory = None
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
        callback = make_textract_reconciliation_callback(
            session_factory=get_session_factory(), provider=provider, stager=stager
        )

        async def provider_worker() -> None:
            await run_provider_job_reconciliation_processor_forever(
                get_session_factory(),
                reconcile_callback=callback,
                max_attempts=extraction_config.reconciliation_max_attempts,
                window_seconds=extraction_config.reconciliation_window_seconds,
                poll_interval_seconds=extraction_config.reconciliation_interval_seconds,
                batch_size=extraction_config.reconciliation_batch_size,
                shutdown_event=provider_reconciliation_shutdown_event,
            )

        provider_worker_factory = provider_worker

    outbox_shutdown_event = asyncio.Event()
    failure_quarantine_shutdown_event = asyncio.Event()
    provider_reconciliation_shutdown_event = (
        asyncio.Event() if provider_worker_factory is not None else None
    )

    async def outbox_worker() -> None:
        await run_outbox_processor_forever(
            get_session_factory(), shutdown_event=outbox_shutdown_event
        )

    async def failure_quarantine_worker() -> None:
        await run_failure_quarantine_processor_forever(
            get_session_factory(), shutdown_event=failure_quarantine_shutdown_event
        )

    outbox_task = asyncio.create_task(
        _supervise_worker("audit_outbox", outbox_worker, outbox_shutdown_event),
        name="nexa-audit-outbox-supervisor",
    )
    failure_quarantine_task = asyncio.create_task(
        _supervise_worker(
            "failure_quarantine",
            failure_quarantine_worker,
            failure_quarantine_shutdown_event,
        ),
        name="nexa-failure-quarantine-supervisor",
    )
    provider_reconciliation_task = None
    if provider_worker_factory is not None and provider_reconciliation_shutdown_event is not None:
        provider_reconciliation_task = asyncio.create_task(
            _supervise_worker(
                "provider_reconciliation",
                provider_worker_factory,
                provider_reconciliation_shutdown_event,
            ),
            name="nexa-provider-reconciliation-supervisor",
        )

    application.state.audit_outbox_task = outbox_task
    application.state.failure_quarantine_task = failure_quarantine_task
    application.state.provider_reconciliation_task = provider_reconciliation_task
    application.state.provider_reconciliation_required = provider_worker_factory is not None

    try:
        yield
    finally:
        await _stop_worker(
            "provider_reconciliation",
            provider_reconciliation_task,
            provider_reconciliation_shutdown_event,
        )
        application.state.provider_reconciliation_task = None
        await _stop_worker(
            "failure_quarantine",
            failure_quarantine_task,
            failure_quarantine_shutdown_event,
        )
        application.state.failure_quarantine_task = None
        await _stop_worker("audit_outbox", outbox_task, outbox_shutdown_event)
        application.state.audit_outbox_task = None

        try:
            await get_async_engine().dispose()
        except Exception as exc:
            log_safe_exception(
                logger, exc, subsystem="database", operation="shutdown_dispose"
            )
        try:
            await get_async_redis_client().close()
        except Exception as exc:
            log_safe_exception(logger, exc, subsystem="redis", operation="shutdown_close")


app = FastAPI(title="Nexa Care API", version="0.2.1", lifespan=lifespan)

app.add_middleware(ContentSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CookieCsrfMiddleware)

_cors_origins = [
    item.strip()
    for item in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if item.strip()
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
    host.strip()
    for host in os.getenv(
        "TRUSTED_HOSTS", "localhost,127.0.0.1,testserver"
    ).split(",")
    if host.strip()
] or ["localhost", "127.0.0.1", "testserver"]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)
app.add_middleware(GlobalLoggingMiddleware)

app.include_router(api_router)
app.include_router(auth_v2_router)
app.include_router(consent_v2_router)
app.include_router(consent_v3_router)
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
app.include_router(patient_discovery_v2_router)
app.include_router(provider_trust_v2_router)
app.include_router(provider_trust_permission_v2_router)

app.add_exception_handler(ProviderTrustRouteError, provider_trust_route_error_response)
app.add_exception_handler(
    ProviderTrustPermissionRouteError, provider_trust_permission_route_error_response
)


@app.exception_handler(PatientDataErased)
async def patient_data_erased_handler(request: Request, exc: PatientDataErased):
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={
            "error_code": "PATIENT_DATA_ERASED",
            "message": "Patient encrypted data is no longer available.",
        },
    )


@app.exception_handler(ErasureRegistryUnavailable)
async def erasure_registry_unavailable_handler(
    request: Request, exc: ErasureRegistryUnavailable
):
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
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        _REQUESTS_TOTAL.labels(
            method=request.method, status_code=str(response.status_code)
        ).inc()
        _REQUEST_DURATION.labels(method=request.method).observe(duration)
        return response


app.add_middleware(PrometheusMiddleware)


def _operations_access(request: Request) -> None:
    expected = get_operations_auth_token()
    if expected is None:
        return
    supplied = request.headers.get("x-nexa-operations-token", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _worker_status(task: asyncio.Task | None) -> str:
    return "ok" if task is not None and not task.done() else "unavailable"


def _outbox_limits() -> tuple[int, int, int] | None:
    try:
        values = (
            int(os.getenv("AUDIT_OUTBOX_MAX_DEAD_LETTERS", "0")),
            int(os.getenv("AUDIT_OUTBOX_MAX_EXPIRED_LEASES", "0")),
            int(os.getenv("AUDIT_OUTBOX_MAX_PENDING_AGE_SECONDS", "300")),
        )
    except ValueError:
        return None
    if any(value < 0 for value in values):
        return None
    return values


async def _readiness_snapshot(*, detailed: bool, include_aws: bool) -> dict:
    checks: dict[str, str] = {}
    details: dict[str, object] = {}

    try:
        await get_async_redis_client().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    outbox_health = None
    try:
        engine = get_async_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
        async with get_session_factory()() as db:
            outbox_health = await get_outbox_health(db)
    except Exception:
        checks["postgres"] = "unavailable"

    limits = _outbox_limits()
    if outbox_health is None or limits is None:
        checks["audit_outbox"] = "unavailable"
    else:
        dead_letter_limit, expired_lease_limit, oldest_pending_limit = limits
        checks["audit_outbox"] = (
            "unhealthy"
            if (
                outbox_health["dead_letter_backlog"] > dead_letter_limit
                or outbox_health["expired_lease_count"] > expired_lease_limit
                or outbox_health["oldest_pending_age_seconds"] > oldest_pending_limit
            )
            else "ok"
        )
        if detailed:
            details["audit_outbox"] = {
                "pending_count": outbox_health["pending_count"],
                "dead_letter_backlog": outbox_health["dead_letter_backlog"],
                "expired_lease_count": outbox_health["expired_lease_count"],
                "oldest_pending_age_seconds": round(
                    outbox_health["oldest_pending_age_seconds"], 3
                ),
            }

    production_like = get_runtime_environment().is_production_like
    worker_details = {
        "audit_outbox": _worker_status(getattr(app.state, "audit_outbox_task", None)),
        "failure_quarantine": _worker_status(
            getattr(app.state, "failure_quarantine_task", None)
        ),
    }
    provider_required = bool(
        getattr(app.state, "provider_reconciliation_required", False)
    )
    provider_task = getattr(app.state, "provider_reconciliation_task", None)
    worker_details["provider_reconciliation"] = (
        _worker_status(provider_task) if provider_required else "disabled"
    )
    required_worker_states = [worker_details["audit_outbox"]]
    if production_like or getattr(app.state, "failure_quarantine_task", None) is not None:
        required_worker_states.append(worker_details["failure_quarantine"])
    if provider_required:
        required_worker_states.append(worker_details["provider_reconciliation"])
    checks["workers"] = (
        "ok" if all(value == "ok" for value in required_worker_states) else "unavailable"
    )
    if detailed:
        details["workers"] = worker_details

    if include_aws:
        try:
            await verify_aws_runtime()
            checks["aws"] = "ok"
        except Exception:
            checks["aws"] = "unavailable"

    required = ["redis", "postgres", "audit_outbox", "workers"]
    if include_aws:
        required.append("aws")
    overall = "ok" if all(checks.get(name) == "ok" for name in required) else "degraded"
    payload: dict[str, object] = {"status": overall, "checks": checks}
    if detailed:
        report = getattr(app.state, "runtime_preflight_report", None)
        if isinstance(report, RuntimePreflightReport):
            details["startup_preflight"] = {
                "production_like": report.production_like,
                "migration_head": report.migration_head,
                "checks": list(report.checks),
            }
        payload["details"] = details
    return payload


@app.get("/healthz", tags=["health"])
async def liveness_check() -> dict:
    return {"status": "ok"}


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Public readiness exposes only coarse, non-diagnostic dependency state."""

    payload = await _readiness_snapshot(detailed=False, include_aws=False)
    if payload["status"] == "ok":
        return payload
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=payload)


@app.get("/ops/health", tags=["operations"], include_in_schema=False)
async def operations_health(request: Request):
    """Protected detailed health for operators; still contains no secret values."""

    _operations_access(request)
    payload = await _readiness_snapshot(detailed=True, include_aws=True)
    return JSONResponse(
        status_code=(200 if payload["status"] == "ok" else 503), content=payload
    )


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Protected Prometheus exposition endpoint."""

    _operations_access(request)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
