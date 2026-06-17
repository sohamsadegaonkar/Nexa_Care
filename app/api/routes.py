"""API router for Nexa Care endpoints."""
from fastapi.params import Security

from app.api.auth_deps import verify_provider
from uuid import UUID, uuid4
from app.observability.redactor import redact_payload
from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from app.observability.audit_ledger import append_audit_log
from app.core.redis import issue_token, validate_token
from app.core.supabase import get_supabase_client
from app.services.crypto_engine import process_biometric_handshake
from app.services.auth_service import validate_session_context
from app.models.schemas import (
    ClinicalRecordSchema,
    PIIVaultSchema,
    RegisterResponse,
    UnifiedPatientPayload,
)

router = APIRouter()


class ConsentRequest(BaseModel):
    masked_internal_id: UUID
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)  # max 24h


class HandshakePayload(BaseModel):
    nfc_uid: str
    bio_seed: str
    masked_internal_id: str

@router.post("/api/v1/handshake", tags=["auth"])
async def process_handshake(payload: HandshakePayload, provider_key: str = Security(verify_provider)):
    """
    Biometric Handshake Protocol:
    Collides the NFC UID (Helper String) with the live pulse (Bio Seed)
    to generate an ephemeral session token via the Crypto Engine.
    """
    # Execute the cryptographic collision
    auth_result = await process_biometric_handshake(
        nfc_uid=payload.nfc_uid,
        bio_seed=payload.bio_seed,
        masked_internal_id=payload.masked_internal_id,
    )

    # Cryptographic rejection or missing data
    if auth_result is None:
        raise HTTPException(
            status_code=400,
            detail="Biometric match alignment configuration failure."
        )

    # Return the Upstash Redis Session Token
    return auth_result
@router.get("/api/v1/record/{masked_internal_id}", tags=["reassembly"])
async def get_record(
    masked_internal_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """Layer 3: Reassembly Engine.

    Requires: Authorization: Bearer <session_token>
    - Validates the session in Redis (Ghost Key session)
    - Confirms the session is scoped to *this* masked_internal_id -- a session
      from a handshake for patient A cannot be used to read patient B's record
    - Reassembles (stitches) the canonical flat PII + clinical columns from
      Supabase shards (the same columns /register and /view-record use, so a
      record looks identical here regardless of which endpoint created it)
    """

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_ATTEMPT",
        target_id=masked_internal_id,
        status="STARTED",
    )

    session_context = await validate_session_context(authorization)
    if not session_context or not session_context.get("authenticated"):
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="UNAUTHORIZED",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid Ghost Key.",
        )

    if session_context.get("masked_internal_id") != masked_internal_id:
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="FORBIDDEN_SCOPE_MISMATCH",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session is not authorized for this record.",
        )

    supabase = get_supabase_client()

    vault_res = (
        supabase.table("nexa_vault")
        .select("patient_name,phone,aadhaar_abha_id,masked_internal_id")
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

    vault = vault_rows[0] or {}
    clinical = clinical_rows[0] or {}

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_COMPLETED",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    # Stitching: unified payload (do not persist)
    raw_response = {
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

    # STRATEGIC CHOKEPOINT: Mask sensitive data before it leaves the server
    raw_response["pii"] = redact_payload(raw_response["pii"])

    return raw_response


@router.post("/register", response_model=RegisterResponse, tags=["sharding"])
async def register_patient(payload: UnifiedPatientPayload, provider_key: str = Security(verify_provider)) -> RegisterResponse:
    """
    Registers a patient by creating a shared masked_internal_id and persisting:
    - PII to nexa_vault
    - Clinical data to nexa_clinical
    """
    masked_internal_id = uuid4()

    await append_audit_log(
        actor_uid="SYSTEM_INGEST",
        event_type="PATIENT_REGISTRATION_ATTEMPT",
        target_id=str(masked_internal_id),
        status="STARTED",
    )

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
        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_FAILED",
            target_id=str(masked_internal_id),
            status="CRITICAL_ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "vault_error": str(getattr(vault_res, "error", None)),
                "clinical_error": str(getattr(clinical_res, "error", None)),
            },
        )

    await append_audit_log(
        actor_uid="SYSTEM_INGEST",
        event_type="PATIENT_REGISTRATION_SUCCESS",
        target_id=str(masked_internal_id),
        status="SUCCESS",
    )

    return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)


@router.post("/request-consent", tags=["consent"])
async def request_consent(payload: ConsentRequest, provider_key: str = Security(verify_provider)) -> dict:
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
        .select("patient_name,phone,aadhaar_abha_id,masked_internal_id")
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
        # Pre-assemble the raw payload
    raw_response = {
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

    # STRATEGIC CHOKEPOINT: Mask sensitive data before it leaves the server
    raw_response["pii"] = redact_payload(raw_response["pii"])

    return raw_response