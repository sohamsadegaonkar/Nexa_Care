"""API router for Nexa Care endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.redis import issue_token, validate_token
from app.core.supabase import get_supabase_client
from app.observability.audit_ledger import append_audit_log
from app.services.auth_service import validate_session_context
from app.services.crypto_engine import process_biometric_handshake
from app.models.schemas import UnifiedPatientPayload

router = APIRouter()


class ConsentRequest(BaseModel):
    masked_internal_id: UUID
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)  # max 24h


class HandshakePayload(BaseModel):
    nfc_uid: str
    bio_seed: str


@router.post("/api/v1/handshake", tags=["auth"])
async def process_handshake(payload: HandshakePayload):
    """
    Biometric Handshake Protocol:
    Collides the NFC UID (Helper String) with the live pulse (Bio Seed)
    to generate an ephemeral session token via the Crypto Engine.
    """
    # 1. Route the payload to the cryptographic engine
    auth_result = await process_biometric_handshake(
        nfc_uid=payload.nfc_uid,
        bio_seed=payload.bio_seed,
    )

    # 2. Defensive check: reject on bad/missing signal
    if auth_result is None:
        raise HTTPException(
            status_code=400,
            detail="Biometric match alignment configuration failure.",
        )

    # 3. Return the generated Upstash Redis session token to the client
    return auth_result


@router.post("/register", tags=["sharding"])
async def register_patient(payload: UnifiedPatientPayload):
    """Registers a patient: writes PII to nexa_vault and clinical data to
    nexa_clinical under a shared masked_internal_id, with an audit log
    entry at each stage of the attempt."""

    await append_audit_log(
        actor_uid="SYSTEM_INGEST",
        event_type="PATIENT_REGISTRATION_ATTEMPT",
        target_id="PENDING_GENERATION",
        status="STARTED",
    )

    try:
        supabase = get_supabase_client()
        masked_internal_id = uuid4()

        vault_response = (
            supabase.table("nexa_vault")
            .insert(
                {
                    "masked_internal_id": str(masked_internal_id),
                    "patient_name": payload.patient_name,
                    "phone": payload.phone,
                    "aadhaar_abha_id": payload.aadhaar_abha_id,
                }
            )
            .execute()
        )

        clinical_response = (
            supabase.table("nexa_clinical")
            .insert(
                {
                    "masked_internal_id": str(masked_internal_id),
                    "diagnoses": payload.diagnoses,
                    "lab_results": payload.lab_results,
                    "prescriptions": payload.prescriptions,
                }
            )
            .execute()
        )

        if getattr(vault_response, "error", None) or getattr(clinical_response, "error", None):
            raise RuntimeError("Supabase insert failed during patient registration")

        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_SUCCESS",
            target_id=str(masked_internal_id),
            status="SUCCESS",
        )

        return {"masked_internal_id": str(masked_internal_id), "status": "registered"}

    except Exception:
        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_FAILED",
            target_id="FAILED_GENERATION",
            status="CRITICAL_ERROR",
        )
        raise


@router.get("/api/v1/record/{masked_internal_id}", tags=["reassembly"])
async def get_record(masked_internal_id: str, authorization: str | None = Header(default=None)):
    """Layer 3: Reassembly Engine.

    Requires: Authorization: Bearer <session_token>
    Validates the session via auth_service (Redis-backed), then stitches
    raw_pii + clinical_data from the Supabase shards. Every attempt is
    written to the audit ledger.
    """

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_ATTEMPT",
        target_id=masked_internal_id,
        status="STARTED",
    )

    if not authorization:
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="UNAUTHORIZED",
        )
        raise HTTPException(status_code=401, detail="Missing authorization token")

    session_context = await validate_session_context(authorization)
    if not session_context:
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="UNAUTHORIZED",
        )
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    supabase = get_supabase_client()

    vault_response = (
        supabase.table("nexa_vault")
        .select("*")
        .eq("masked_internal_id", masked_internal_id)
        .single()
        .execute()
    )

    clinical_response = (
        supabase.table("nexa_clinical")
        .select("*")
        .eq("masked_internal_id", masked_internal_id)
        .single()
        .execute()
    )

    if getattr(vault_response, "error", None) or getattr(clinical_response, "error", None):
        raise HTTPException(status_code=404, detail="Record not found")

    vault_data = getattr(vault_response, "data", None)
    clinical_data = getattr(clinical_response, "data", None)

    if not vault_data or not clinical_data:
        raise HTTPException(status_code=404, detail="Record not found")

    reassembled_record = {
        "masked_internal_id": masked_internal_id,
        "identity": vault_data,
        "clinical": clinical_data,
    }

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_COMPLETED",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    return reassembled_record


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