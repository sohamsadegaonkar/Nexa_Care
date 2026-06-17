"""API router for Nexa Care endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

# Core & Services
from app.observability.audit_ledger import append_audit_log
from app.core.redis import get_redis_client, issue_token, validate_token
from app.core.supabase import get_supabase_client
from app.services.crypto_engine import process_biometric_handshake
from app.services.auth_service import validate_session_context

# Schemas
from app.models.schemas import (
    ClinicalRecordSchema,
    PIIVaultSchema,
    RegisterResponse,
    UnifiedPatientPayload,
)

router = APIRouter()

# ------------------------------------------------------------------
# HELPER FUNCTIONS & MODELS
# ------------------------------------------------------------------

def _extract_bearer_token(authorization: str | None) -> str | None:
    """Parse `Authorization: Bearer <token>` header."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2:
        return None
    scheme, token = parts[0], parts[1]
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()

class ConsentRequest(BaseModel):
    masked_internal_id: UUID
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)  # max 24h

class HandshakePayload(BaseModel):
    nfc_uid: str
    bio_seed: str

# ------------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------------

@router.post("/api/v1/handshake", tags=["auth"])
async def process_handshake(payload: HandshakePayload):
    """
    Biometric Handshake Protocol:
    Collides the NFC UID (Helper String) with the live pulse (Bio Seed)
    to generate an ephemeral session token via the Crypto Engine.
    """
    auth_result = await process_biometric_handshake(
        nfc_uid=payload.nfc_uid,
        bio_seed=payload.bio_seed
    )

    if auth_result is None:
        raise HTTPException(
            status_code=400,
            detail="Biometric match alignment configuration failure."
        )

    return auth_result


@router.get("/api/v1/record/{masked_internal_id}", tags=["reassembly"])
async def get_record(
    masked_internal_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """Layer 3: Reassembly Engine with Cryptographic Audit Tracking."""
    
    # [AUDIT LOG]: Intent to read
    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_ATTEMPT",
        target_id=masked_internal_id,
        status="STARTED",
    )

    session_token = _extract_bearer_token(authorization)
    if session_token is None:
        await append_audit_log(actor_uid="REASSEMBLY_ENGINE", event_type="RECORD_ACCESS_DENIED", target_id=masked_internal_id, status="UNAUTHORIZED")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid Ghost Key.")

    # Redis verification
    redis_client = get_redis_client()
    alpha = redis_client.get(session_token)
    if not alpha:
        await append_audit_log(actor_uid="REASSEMBLY_ENGINE", event_type="RECORD_ACCESS_DENIED", target_id=masked_internal_id, status="UNAUTHORIZED")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid Ghost Key.")

    supabase = get_supabase_client()

    vault_res = supabase.table("nexa_vault").select("patient_name,phone,aadhaar_abha_id,masked_internal_id").eq("masked_internal_id", masked_internal_id).limit(1).execute()
    clinical_res = supabase.table("nexa_clinical").select("diagnoses,lab_results,prescriptions,masked_internal_id").eq("masked_internal_id", masked_internal_id).limit(1).execute()

    if getattr(vault_res, "error", None) or getattr(clinical_res, "error", None):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"vault_error": str(getattr(vault_res, "error", None)), "clinical_error": str(getattr(clinical_res, "error", None))},
        )

    vault_rows = getattr(vault_res, "data", None) or []
    clinical_rows = getattr(clinical_res, "data", None) or []

    if not vault_rows or not clinical_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found for masked_internal_id")

    vault = vault_rows[0] or {}
    clinical = clinical_rows[0] or {}

    # [AUDIT LOG]: Cryptographic loop closed successfully
    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_COMPLETED",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    return {
        "masked_internal_id": masked_internal_id,
        "pii": {
            "patient_name": vault.get("patient_name"),
            "phone": vault.get("phone"),
            "aadhaar_abha_id": vault.get("aadhaar_abha_id"),
        },
        "clinical": {
            "diagnoses": clinical.get("diagnoses") or [],
            "lab_results": clinical.get("lab_results") or [],
            "prescriptions": clinical.get("prescriptions") or [],
        },
    }


@router.post("/register", response_model=RegisterResponse, tags=["sharding"])
async def register_patient(payload: UnifiedPatientPayload) -> RegisterResponse:
    """Registers a patient and logs the transaction to the immutable ledger."""
    
    # [AUDIT LOG]: Write initiation
    await append_audit_log(
        actor_uid="SYSTEM_INGEST",
        event_type="PATIENT_REGISTRATION_ATTEMPT",
        target_id="PENDING_GENERATION",
        status="STARTED",
    )

    try:
        # Generate ID dynamically using uuid4 (Fixes the Pylance Error)
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

        supabase = get_supabase_client()

        vault_res = supabase.table("nexa_vault").insert({
            "masked_internal_id": str(masked_internal_id),
            "patient_name": pii_vault.patient_name,
            "phone": pii_vault.phone,
            "aadhaar_abha_id": pii_vault.aadhaar_abha_id,
        }).execute()

        clinical_res = supabase.table("nexa_clinical").insert({
            "masked_internal_id": str(masked_internal_id),
            "diagnoses": clinical_record.diagnoses,
            "lab_results": clinical_record.lab_results,
            "prescriptions": clinical_record.prescriptions,
        }).execute()

        if getattr(vault_res, "error", None) or getattr(clinical_res, "error", None):
            raise RuntimeError("Supabase insert failed during patient registration")

        # [AUDIT LOG]: Confirmed transaction persistence
        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_SUCCESS",
            target_id=str(masked_internal_id),
            status="SUCCESS",
        )

        return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)

    except Exception as e:
        # [AUDIT LOG]: Record systemic failure state
        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_FAILED",
            target_id="FAILED_GENERATION",
            status="CRITICAL_ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Database transaction aborted: {str(e)}"
        )


@router.post("/request-consent", tags=["consent"])
async def request_consent(payload: ConsentRequest) -> dict:
    """Issues a time-bound consent token stored in Upstash Redis."""
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
    """
    Zero-trust retrieval:
    - Accept consent token via header or query param
    - Validate token in Redis
    - If valid, fetch from both Supabase shards and merge into one response
    """
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