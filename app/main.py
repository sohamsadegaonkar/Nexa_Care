"""Nexa Care FastAPI entrypoint."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.api.routes import router as api_router
from app.core.config import (
    get_handshake_security_config,
    get_redis_config,
    get_supabase_config,
)
from app.core.supabase import get_supabase_client
from app.services.sharding import split_pii_and_clinical_fields
from document_processor import extract_document_data
from app.middleware.logging_middleware import GlobalLoggingMiddleware

load_dotenv()  # loads .env into os.environ if present

logger = logging.getLogger("nexa_logger")

app = FastAPI(title="Nexa Care API", version="0.1.0")
app.add_middleware(GlobalLoggingMiddleware)


@app.on_event("startup")
async def _validate_required_config() -> None:
    """Fail fast if required secrets are not present."""

    get_supabase_config()
    get_redis_config()
    get_handshake_security_config()


app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/process-document", tags=["documents"])
async def process_document(file: UploadFile = File(...)) -> dict:
    """Process an uploaded document and vertically shard PII + clinical layout data."""

    suffix = os.path.splitext(file.filename or "")[1] or ".png"
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
            contents = await file.read()
            tmp.write(contents)

        # extract_document_data runs CPU-bound model inference synchronously.
        # Calling it directly here would block this worker's event loop for
        # the full duration, stalling every other concurrent request on the
        # same worker. run_in_threadpool offloads it to FastAPI/Starlette's
        # worker thread pool instead. This is a stopgap: celery + flower are
        # already project dependencies and are the better long-term home
        # for this work once extraction volume grows enough that the thread
        # pool itself becomes the bottleneck -- that's a bigger change
        # (the endpoint would need to return a job id instead of the result
        # directly), so it's being called out rather than done silently here.
        document_data = await run_in_threadpool(extract_document_data, temp_path)
        if not document_data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to extract document data",
            )

        vault_payload, clinical_payload, unrecognized_payload = split_pii_and_clinical_fields(
            document_data
        )

        if unrecognized_payload:
            logger.warning(json.dumps({
                "event": "extraction_schema_mismatch",
                "unrecognized_keys": sorted(unrecognized_payload.keys()),
            }))
            # Fail safe: an unrecognized key might be undocumented PII --
            # this is exactly how aadhaar_abha_id almost ended up in the
            # "anonymized" clinical shard, just under a name the old check
            # didn't expect. Quarantine it in the more restrictively
            # accessed vault rather than assuming it's safe. See
            # scripts/validate_extraction_schema.py for how to find and
            # clear these against a real labeled document set instead of
            # letting them silently route to the vault indefinitely.
            vault_payload = {**vault_payload, **unrecognized_payload}

        masked_internal_id = str(uuid.uuid4())

        supabase = get_supabase_client()

        vault_res = (
            supabase.table("nexa_vault")
            .insert(
                {
                    "masked_internal_id": masked_internal_id,
                    "raw_pii": vault_payload,
                }
            )
            .execute()
        )
        clinical_res = (
            supabase.table("nexa_clinical")
            .insert(
                {
                    "masked_internal_id": masked_internal_id,
                    "clinical_data": clinical_payload,
                }
            )
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
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)