"""Nexa Care FastAPI entrypoint."""
from __future__ import annotations

import os
import tempfile
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, status

from app.api.routes import router as api_router
from app.core.config import get_redis_config, get_supabase_config
from app.core.supabase import get_supabase_client
from document_processor import (
    analyze_and_extract_tables,
    analyze_document_layout,
    extract_standard_text,
)

load_dotenv()  # loads .env into os.environ if present

app = FastAPI(title="Nexa Care API", version="0.1.0")


@app.on_event("startup")
async def _validate_required_config() -> None:
    """Fail fast if required secrets are not present."""

    get_supabase_config()
    get_redis_config()


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

        layout_data = analyze_document_layout(temp_path)
        if layout_data is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to analyze document layout",
            )

        pii_text = await extract_standard_text(temp_path, layout_data)
        if pii_text is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to extract PII text",
            )

        table_grids = analyze_and_extract_tables(temp_path)

        masked_internal_id = str(uuid.uuid4())

        supabase = get_supabase_client()

        vault_res = (
            supabase.table("nexa_vault")
            .insert({"masked_internal_id": masked_internal_id, "raw_pii": pii_text})
            .execute()
        )
        clinical_res = (
            supabase.table("nexa_clinical")
            .insert(
                {
                    "masked_internal_id": masked_internal_id,
                    "clinical_data": table_grids,
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

        return {
            "masked_internal_id": masked_internal_id,
            "tables_detected": len(layout_data.get("tables", [])),
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
