"""API router for Nexa Care endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.redis import issue_token, validate_token
from app.core.supabase import get_supabase_client
from app.models.schemas import (
    ClinicalRecordSchema,
    PIIVaultSchema,
    RegisterResponse,
    UnifiedPatientPayload,
)

router = APIRouter()


class ConsentRequest(BaseModel):
    masked_internal_id: UUID
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)


@router.post("/register", response_model=RegisterResponse, tags=["sharding"])
async def register_patient(payload: UnifiedPatientPayload) -> RegisterResponse:
    """Create linked records in Supabase shards (vault + clinical)."""

    masked_internal_id = uuid4()

    pii_vault = PIIVaultSchema(
        masked_internal_id=masked_internal_id,
        patient_name=payload.patient_name,
        phone=payload.phone,
        aadhaar_abha_id=payload.aadhaar_abha_id,
    )
    clinical_record = ClinicalRecordSchema(
        masked_internal_id=masked_internal_id,
        diagnoses=payload.diagnoses,
        lab_results=payload.lab_results,
        prescriptions=payload.prescriptions,
    )

    # Persist to Supabase
    supabase = get_supabase_client()

    vault_res = (
        supabase.table("nexa_vault")
        .insert(
            {
                "masked_internal_id": str(masked_internal_id),
                "patient_name": pii_vault.patient_name,
                "phone": pii_vault.phone,
                "aadhaar_abha_id": pii_vault.aadhaar_abha_id,
            }
        )
        .execute()
    )
    clinical_res = (
        supabase.table("nexa_clinical")
        .insert(
            {
                "masked_internal_id": str(masked_internal_id),
                "diagnoses": clinical_record.diagnoses,
                "lab_results": clinical_record.lab_results,
                "prescriptions": clinical_record.prescriptions,
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

    return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)


@router.post("/request-consent", tags=["consent"])
async def request_consent(payload: ConsentRequest) -> dict:
    """Issue a time-bound consent token stored in Upstash Redis."""

    consent_token = issue_token(
        masked_internal_id=str(payload.masked_internal_id),
        ttl_seconds=payload.duration_seconds,
    )
    return {"consent_token": consent_token, "expires_in": payload.duration_seconds}


@router.get("/view-record", tags=["consent"])
async def view_record(
    consent_token_header: str | None = Header(default=None, alias="X-Consent-Token"),
    consent_token_query: str | None = Query(default=None, alias="consent_token"),
) -> dict:
    """Zero-trust retrieval gated by a Redis-backed consent token."""

    consent_token = consent_token_header or consent_token_query
    masked_internal_id = validate_token(consent_token) if consent_token else None

    if masked_internal_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    supabase = get_supabase_client()

    vault_res = (
        supabase.table("nexa_vault")
        .select("patient_name,phone,masked_internal_id")
        .eq("masked_internal_id", masked_internal_id)
        .limit(1)
        .execute()
    )
    clinical_res = (
        supabase.table("nexa_clinical")
        .select("diagnoses,lab_results,prescriptions,masked_internal_id")
        .eq("masked_internal_id", masked_internal_id)
        .limit(1)
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

    vault_rows = getattr(vault_res, "data", None) or []
    clinical_rows = getattr(clinical_res, "data", None) or []

    if not vault_rows or not clinical_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Record not found for masked_internal_id",
        )

    vault = vault_rows[0]
    clinical = clinical_rows[0]

    return {
        "masked_internal_id": masked_internal_id,
        "patient_name": vault.get("patient_name"),
        "phone": vault.get("phone"),
        "diagnoses": clinical.get("diagnoses"),
        "lab_results": clinical.get("lab_results"),
        "prescriptions": clinical.get("prescriptions"),
    }
