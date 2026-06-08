"""API router for Nexa Care endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from app.observability.audit_ledger import append_audit_lo
from app.core.handshake import create_secure_session, generate_soham_alpha
from app.core.redis import get_redis_client, issue_token, validate_token
from app.core.supabase import get_supabase_client
from app.models.schemas import (
    ClinicalRecordSchema,
    PIIVaultSchema,
    RegisterResponse,
    UnifiedPatientPayload,
)

router = APIRouter()


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


class HandshakeRequest(BaseModel):
    nfc_uid: str
    bio_seed: str


@router.post("/api/v1/handshake", tags=["auth"])
async def process_handshake(request: HandshakeRequest) -> dict:
    alpha = generate_soham_alpha(request.nfc_uid, request.bio_seed)
    session_token = create_secure_session(alpha)
    return {
        "session_token": session_token,
        "message": "Ghost Key generated. Expires in 30 minutes.",
    }


@router.get("/api/v1/record/{masked_internal_id}", tags=["reassembly"])
async def get_record(
    masked_internal_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """Layer 3: Reassembly Engine.

    Requires: Authorization: Bearer <session_token>
    - Validates token existence in Redis (Ghost Key session)
    - Reassembles (stitches) raw_pii + clinical_data from Supabase shards
    """

    session_token = _extract_bearer_token(authorization)
    if session_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid Ghost Key.",
        )

    # Redis verification: session must exist and not be expired
    redis_client = get_redis_client()
    alpha = redis_client.get(session_token)
    if not alpha:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid Ghost Key.",
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Record not found for masked_internal_id",
        )

    vault = vault_rows[0] or {}
    clinical = clinical_rows[0] or {}

    raw_pii = vault.get("raw_pii") or {}
    clinical_data = clinical.get("clinical_data") or {}

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
@router.post("/register")
async def register_patient(payload: UnifiedPatientPayload):
    await append_audit_log(
        actor_uid="SYSTEM_INGEST",
        event_type="PATIENT_REGISTRATION_ATTEMPT",
        target_id="PENDING_GENERATION",
        status="STARTED",
    )

    try:
        supabase = get_supabase_client()
        masked_internal_id = generate_masked_internal_id()

        vault_response = (
            supabase.table("nexa_vault")
            .insert(
                {
                    "masked_internal_id": str(masked_internal_id),
                    "full_name": payload.full_name,
                    "date_of_birth": payload.date_of_birth,
                    "phone": payload.phone,
                    "email": payload.email,
                    "address": payload.address,
                }
            )
            .execute()
        )

        clinical_response = (
            supabase.table("nexa_clinical")
            .insert(
                {
                    "masked_internal_id": str(masked_internal_id),
                    "blood_group": payload.blood_group,
                    "allergies": payload.allergies,
                    "diagnoses": payload.diagnoses,
                    "medications": payload.medications,
                    "clinical_notes": payload.clinical_notes,
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


@router.get("/api/v1/record/{masked_internal_id}")
async def get_record(masked_internal_id: str, authorization: str | None = Header(default=None)):
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
        