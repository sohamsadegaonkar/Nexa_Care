"""API router for Nexa Care endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from app.observability.audit_ledger import append_audit_log
from app.core.handshake import create_secure_session, generate_soham_alpha
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
    # The patient record this handshake is being performed to unlock.
    # The resulting session is permanently bound to this id (see
    # crypto_engine.process_biometric_handshake) -- it cannot later be
    # used to read any other patient's record.
    masked_internal_id: UUID


@router.post("/api/v1/handshake", tags=["auth"])
async def process_handshake(payload: HandshakePayload):
    """
    Biometric Handshake Protocol:
    Collides the NFC UID (Helper String) with the live pulse (Bio Seed)
    to generate an ephemeral session token via the Crypto Engine.

    The issued session is scoped to `masked_internal_id` only -- it
    authorizes access to that one patient record, not the system at large.
    """
    auth_result = await process_biometric_handshake(
        nfc_uid=payload.nfc_uid,
        bio_seed=payload.bio_seed,
        masked_internal_id=str(payload.masked_internal_id),
    )

    if auth_result is None:
        raise HTTPException(
            status_code=400,
            detail="Biometric match alignment configuration failure.",
        )

    return auth_result


@router.get("/api/v1/record/{masked_internal_id}", tags=["reassembly"])
async def get_record(
    masked_internal_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """Layer 3: Reassembly Engine.

    Requires: Authorization: Bearer <session_token>
    - Validates the session token issued by /api/v1/handshake
    - Enforces that the session is scoped to THIS masked_internal_id --
      a session minted for one patient cannot be replayed against another
    - Reassembles (stitches) raw_pii + clinical_data from Supabase shards
    """

    if not authorization:
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="MISSING_TOKEN",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid Ghost Key.",
        )

    session_context = await validate_session_context(authorization)
    if not session_context:
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="INVALID_OR_EXPIRED_TOKEN",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid Ghost Key.",
        )

    try:
        requested_id = str(UUID(masked_internal_id))
    except ValueError:
        requested_id = masked_internal_id

    if session_context.get("masked_internal_id") != requested_id:
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="SESSION_SCOPE_MISMATCH",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This session is not authorized for the requested record.",
        )

    supabase = get_supabase_client()

    vault_res = (
        supabase.table("nexa_vault")
        .select("raw_pii,masked_internal_id")
        .eq("masked_internal_id", masked_internal_id)
        .limit(1)
        .execute()
    )

    clinical_res = (
        supabase.table("nexa_clinical")
        .select("clinical_data,masked_internal_id")
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
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_DENIED",
            target_id=masked_internal_id,
            status="RECORD_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Record not found for masked_internal_id",
        )

    vault = vault_rows[0] or {}
    clinical = clinical_rows[0] or {}

    raw_pii = vault.get("raw_pii") or {}
    clinical_data = clinical.get("clinical_data") or {}

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_SUCCESS",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    # Stitching: unified payload (do not persist)
    return {
        "masked_internal_id": masked_internal_id,
        "pii": raw_pii,
        "clinical": clinical_data,
    }


@router.post("/register", response_model=RegisterResponse, tags=["sharding"])
async def register_patient(payload: UnifiedPatientPayload) -> RegisterResponse:
    """
    Registers a patient by creating a shared masked_internal_id and persisting:
    - PII to nexa_vault
    - Clinical data to nexa_clinical
    """
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
async def request_consent(
    payload: ConsentRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """Issues a time-bound consent token stored in Upstash Redis.

    LOCKED DOWN: requires Authorization: Bearer <session_token> from
    /api/v1/handshake, and that session must be scoped to the SAME
    masked_internal_id being requested. A handshake performed for patient A
    can no longer be used to mint a consent token for patient B, and an
    unauthenticated caller can no longer mint a consent token at all.
    """

    if not authorization:
        await append_audit_log(
            actor_uid="CONSENT_ENGINE",
            event_type="CONSENT_REQUEST_DENIED",
            target_id=str(payload.masked_internal_id),
            status="MISSING_TOKEN",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid handshake session is required to request consent.",
        )

    session_context = await validate_session_context(authorization)
    if not session_context:
        await append_audit_log(
            actor_uid="CONSENT_ENGINE",
            event_type="CONSENT_REQUEST_DENIED",
            target_id=str(payload.masked_internal_id),
            status="INVALID_OR_EXPIRED_TOKEN",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid.",
        )

    if session_context.get("masked_internal_id") != str(payload.masked_internal_id):
        await append_audit_log(
            actor_uid="CONSENT_ENGINE",
            event_type="CONSENT_REQUEST_DENIED",
            target_id=str(payload.masked_internal_id),
            status="SESSION_SCOPE_MISMATCH",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This session is not authorized to request consent for the given record.",
        )

    consent_token = issue_token(
        masked_internal_id=str(payload.masked_internal_id),
        ttl_seconds=payload.duration_seconds,
    )

    await append_audit_log(
        actor_uid="CONSENT_ENGINE",
        event_type="CONSENT_TOKEN_ISSUED",
        target_id=str(payload.masked_internal_id),
        status="SUCCESS",
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

    No change needed here for this fix: this endpoint only ever trusts the
    masked_internal_id that was bound to the token at /request-consent time,
    and that issuance path is now locked down.
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