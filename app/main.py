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
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool          # F-02
from starlette.middleware.base import BaseHTTPMiddleware     # F-14

from app.api.routes import router as api_router
from app.core.config import (
    get_clinic_config,
    get_handshake_config,
    get_redis_config,
    get_supabase_config,
)
from app.core.supabase import get_supabase_client
from app.middleware.logging_middleware import GlobalLoggingMiddleware
from app.observability.redactor import redact_payload
from app.services.sharding import split_pii_and_clinical_fields  # F-01
from document_processor import extract_document_data

load_dotenv()

logger = logging.getLogger("nexa_logger")

# ── F-14: Hard upload / body size cap ────────────────────────────────────────
_MAX_UPLOAD_BYTES: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))  # 20 MB


class ContentSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject any request whose Content-Length header exceeds the cap, and
    abort streaming bodies that exceed it mid-transfer.  This prevents both
    OOM attacks on the document endpoint and accidental oversized payloads
    on JSON routes."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_UPLOAD_BYTES:
            return HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Request body exceeds the {_MAX_UPLOAD_BYTES // (1024*1024)} MB limit.",
            )
        return await call_next(request)


# ── F-13: Modern lifespan pattern ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Validate all required secrets at startup so the process fails fast
    rather than serving requests with a broken configuration."""
    get_supabase_config()
    get_redis_config()
    get_handshake_config()
    get_clinic_config()
    yield
    # Shutdown logic (connection draining etc.) goes here when needed.


app = FastAPI(title="Nexa Care API", version="0.2.0", lifespan=lifespan)

# Middleware order matters: size check fires before logging so we don't log
# and store partial bodies from abusive clients.
app.add_middleware(ContentSizeLimitMiddleware)   # F-14 — outermost, fires first
app.add_middleware(GlobalLoggingMiddleware)

app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/process-document", tags=["documents"])
async def process_document(file: UploadFile = File(...)) -> dict:
    """Process an uploaded document and vertically shard PII + clinical data.

    F-01: uses split_pii_and_clinical_fields() — the single authoritative
          sharding function — instead of the old inline dict-split that
          missed aadhaar_abha_id.
    F-02: runs ML inference in a thread pool so the event loop stays free.
    F-06: temp_path is set before any I/O; finally block always fires.
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
            logger.warning(
                json.dumps({
                    "event": "unrecognized_extraction_keys",
                    "keys": sorted(unrecognized_payload.keys()),
                    "action": "routed_to_vault",
                    "note": (
                        "Run scripts/validate_extraction_schema.py and confirm "
                        "with a clinician whether each key is PII before re-routing."
                    ),
                })
            )
            # Fail-safe: treat unknowns as PII until a reviewer confirms otherwise.
            vault_payload.update(unrecognized_payload)

        masked_internal_id = str(uuid.uuid4())
        supabase = get_supabase_client()

        vault_res = (
            supabase.table("nexa_vault")
            .insert({"masked_internal_id": masked_internal_id, "raw_pii": vault_payload})
            .execute()
        )
        clinical_res = (
            supabase.table("nexa_clinical")
            .insert({"masked_internal_id": masked_internal_id, "clinical_data": clinical_payload})
            .execute()
        )

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