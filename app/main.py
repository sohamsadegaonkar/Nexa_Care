"""Nexa Care FastAPI entrypoint."""
from __future__ import annotations

import os
import tempfile
import uuid
from app.api.auth_deps import verify_provider
from fastapi.concurrency import run_in_threadpool
from app.observability.audit_ledger import append_audit_log
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Security, UploadFile, status
from app.api.routes import router as api_router
from app.core.config import get_provider_key, get_redis_config, get_supabase_config
from app.core.supabase import get_supabase_client
from document_processor import extract_document_data
from app.middleware.logging_middleware import GlobalLoggingMiddleware
load_dotenv()  # loads .env into os.environ if present


def _coerce_list(value: object) -> list[str]:
    """Normalize an extractor value into a list[str] for text[] columns."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _split_raw_capture(document_data: dict) -> tuple[dict, dict]:
    """Split the unmapped OCR output into PII-ish vs. clinical-ish raw blobs.

    These land in nexa_vault.raw_pii / nexa_clinical.clinical_data, which are
    real jsonb columns that exist alongside the flat typed ones. They are kept
    as a raw-capture audit trail for the OCR path specifically: extraction is
    lossy and the field set isn't guaranteed, so preserving the untouched
    extractor output means nothing is silently discarded if _map_extracted_fields
    above misses a field or the document contains something unanticipated.
    """
    pii_keys = {"patient_name", "phone", "aadhaar", "aadhaar_abha_id", "email"}
    raw_pii = {k: v for k, v in document_data.items() if k.lower() in pii_keys}
    raw_clinical = {k: v for k, v in document_data.items() if k.lower() not in pii_keys}
    return raw_pii, raw_clinical


def _map_extracted_fields(document_data: dict) -> tuple[dict, dict]:
    """Map OCR-extracted keys onto the canonical nexa_vault / nexa_clinical columns.

    The live Supabase tables use flat typed columns (patient_name, phone,
    aadhaar_abha_id / diagnoses, lab_results, prescriptions) -- the same shape
    used by /register and PIIVaultSchema / ClinicalRecordSchema. This mapping
    keeps the OCR ingestion path writing into that same shape instead of the
    nonexistent raw_pii / clinical_data columns it was targeting before.

    NOTE: the current Donut model is fine-tuned on retail receipts (CORD), not
    clinical documents, so these source keys are unlikely to be populated
    reliably until the model is swapped -- that is a separate, tracked item.
    This function is defensive (missing fields default to "" / []) rather than
    raising, so a partial extraction still produces a row instead of a hard 500.
    """

    patient_name = document_data.get("patient_name", "") or ""
    phone = document_data.get("phone", "") or ""
    # Tolerate the field-name drift already present elsewhere in the codebase
    # (redactor.py uses "aadhaar"; the live table column is "aadhaar_abha_id").
    aadhaar_abha_id = (
        document_data.get("aadhaar_abha_id")
        or document_data.get("aadhaar")
        or ""
    )

    vault_payload = {
        "patient_name": patient_name,
        "phone": phone,
        "aadhaar_abha_id": aadhaar_abha_id,
    }

    clinical_payload = {
        "diagnoses": _coerce_list(document_data.get("diagnoses")),
        "lab_results": _coerce_list(document_data.get("lab_results")),
        "prescriptions": _coerce_list(document_data.get("prescriptions")),
    }

    return vault_payload, clinical_payload

app = FastAPI(title="Nexa Care API", version="0.1.0")
app.add_middleware(GlobalLoggingMiddleware)

@app.on_event("startup")
async def _validate_required_config() -> None:
    """Fail fast if required secrets are not present."""

    get_supabase_config()
    get_redis_config()
    get_provider_key()


app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}

@app.post("/api/v1/process-document", tags=["documents"])
async def process_document(file: UploadFile = File(...), provider_key: str = Security(verify_provider)) -> dict:
    """Process an uploaded document and vertically shard PII + clinical layout data."""
    
    # [AUDIT LOG]: AI Processing Initiated
    await append_audit_log(
        actor_uid="AI_EXTRACTOR",
        event_type="DOCUMENT_PROCESSING_ATTEMPT",
        target_id="PENDING_GENERATION",
        status="STARTED",
    )

    suffix = os.path.splitext(file.filename or "")[1] or ".png"
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
            contents = await file.read()
            tmp.write(contents)

        document_data = await run_in_threadpool(extract_document_data, temp_path)
        if not document_data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to extract document data",
            )

        vault_payload, clinical_payload = _map_extracted_fields(document_data)
        raw_pii, raw_clinical = _split_raw_capture(document_data)

        masked_internal_id = str(uuid.uuid4())

        supabase = get_supabase_client()

        vault_res = (
            supabase.table("nexa_vault")
            .insert({
                "masked_internal_id": masked_internal_id,
                **vault_payload,
                "raw_pii": raw_pii,
            })
            .execute()
        )
        clinical_res = (
            supabase.table("nexa_clinical")
            .insert({
                "masked_internal_id": masked_internal_id,
                **clinical_payload,
                "clinical_data": raw_clinical,
            })
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

        # [AUDIT LOG]: AI Processing Successful
        await append_audit_log(
            actor_uid="AI_EXTRACTOR",
            event_type="DOCUMENT_PROCESSING_SUCCESS",
            target_id=masked_internal_id,
            status="SUCCESS",
        )

        return {"masked_internal_id": masked_internal_id}

    except Exception as e:
        # [AUDIT LOG]: AI Processing Failed
        await append_audit_log(
            actor_uid="AI_EXTRACTOR",
            event_type="DOCUMENT_PROCESSING_FAILED",
            target_id="FAILED_GENERATION",
            status="CRITICAL_ERROR",
        )
        raise e

    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)