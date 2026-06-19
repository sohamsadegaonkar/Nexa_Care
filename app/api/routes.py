"""API router for Nexa Care endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.dependencies import get_scoped_session, verify_provider_token
from app.core.redis import issue_token, validate_token
from app.core.supabase import get_supabase_client
from app.observability.audit_ledger import append_audit_log
from app.services.crypto_engine import process_biometric_handshake
from app.services.biometric_registry import enroll_biometric_binding_with_audit
from app.models.schemas import (
    ClinicalRecordSchema,
    PIIVaultSchema,
    RegisterResponse,
    UnifiedPatientPayload,
)

router = APIRouter()


class ConsentRequest(BaseModel):
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)  # max 24h


class HandshakePayload(BaseModel):
    nfc_uid: str
    bio_seed: str
    masked_internal_id: UUID  # which patient this device/biometric pair is presenting for


class EnrollBiometricPayload(BaseModel):
    masked_internal_id: UUID
    nfc_uid: str
    bio_seed: str


@router.post("/api/v1/handshake", tags=["auth"])
async def process_handshake(payload: HandshakePayload):
    """
    Biometric Handshake Protocol:
    Collides the NFC UID (Helper String) with the live pulse (Bio Seed)
    to generate an ephemeral session token via the Crypto Engine, scoped
    to the patient named in the request.
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


@router.post("/api/v1/enroll-biometric", tags=["auth"], status_code=status.HTTP_201_CREATED)
async def enroll_biometric(
    payload: EnrollBiometricPayload,
    _: None = Depends(verify_provider_token),
):
    """Provider-only: binds an (nfc_uid, bio_seed) pair to a patient's
    masked_internal_id in biometric_registry.

    Gated behind verify_provider_token, same as /register -- a patient
    (or anyone else without the facility credential) cannot self-enroll a
    device against an arbitrary masked_internal_id. This is the single
    action that decides which physical card/biometric a patient identity
    trusts going forward, so it gets the same facility-level gate as
    patient registration, not a lighter one.

    Orchestration (attempt/success/failure audit trail, hard-fail on an
    audit-write failure) lives in
    app.services.biometric_registry.enroll_biometric_binding_with_audit --
    this route is intentionally a thin HTTP adapter over it.
    """
    masked_internal_id = str(payload.masked_internal_id)

    await enroll_biometric_binding_with_audit(
        nfc_uid=payload.nfc_uid,
        bio_seed=payload.bio_seed,
        masked_internal_id=masked_internal_id,
    )

    return {"masked_internal_id": masked_internal_id, "status": "enrolled"}


@router.post("/register", response_model=RegisterResponse, tags=["sharding"])
async def register_patient(
    payload: UnifiedPatientPayload,
    _: None = Depends(verify_provider_token),
) -> RegisterResponse:
    """Registers a patient: writes PII to nexa_vault and clinical data to
    nexa_clinical under a shared masked_internal_id, with an audit log
    entry at each stage. Returns the original {"pii_vault": ...,
    "clinical_record": ...} response contract.

    Gated behind verify_provider_token: patients are registered AT a
    facility, not self-service, so this is a facility-credential route,
    not a patient-session route. See app/core/dependencies.py for the
    distinction between the two trust models in this codebase.
    """

    await append_audit_log(
        actor_uid="SYSTEM_INGEST",
        event_type="PATIENT_REGISTRATION_ATTEMPT",
        target_id="PENDING_GENERATION",
        status="STARTED",
    )

    try:
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

        vault_response = (
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

        clinical_response = (
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

        if getattr(vault_response, "error", None) or getattr(clinical_response, "error", None):
            raise RuntimeError("Supabase insert failed during patient registration")

        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_SUCCESS",
            target_id=str(masked_internal_id),
            status="SUCCESS",
        )

        return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)

    except Exception:
        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_FAILED",
            target_id="FAILED_GENERATION",
            status="CRITICAL_ERROR",
        )
        raise


@router.get("/api/v1/record", tags=["reassembly"])
async def get_record(masked_internal_id: str = Depends(get_scoped_session)):
    """Layer 3: Reassembly Engine.

    masked_internal_id is resolved ONLY from the authenticated session
    (bound at handshake time -- see get_scoped_session). It is never
    accepted from a URL path param, query string, or request body. This
    closes the IDOR where any valid session could read any patient's
    record by editing the id in the URL.

    Stitches the vault + clinical rows into the {"pii": ...,
    "clinical": ...} response contract. Every attempt is written to the
    audit ledger.

    NOTE: nexa_vault/nexa_clinical are written in two different shapes
    depending on which endpoint created the row -- main.py's OCR pipeline
    nests PII under "raw_pii" / clinical under "clinical_data", while
    /register above writes flat columns directly. This handles both so
    callers get a populated "pii"/"clinical" payload either way; the
    underlying dual write-shape is a separate issue worth unifying at the
    data-model level.
    """

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_ATTEMPT",
        target_id=masked_internal_id,
        status="STARTED",
    )

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

    pii_payload = (vault_data.get("raw_pii") if "raw_pii" in vault_data else vault_data) or {}
    clinical_payload = (
        clinical_data.get("clinical_data") if "clinical_data" in clinical_data else clinical_data
    ) or {}

    reassembled_record = {
        "masked_internal_id": masked_internal_id,
        "pii": pii_payload,
        "clinical": clinical_payload,
    }

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_COMPLETED",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    return reassembled_record


@router.post("/request-consent", tags=["consent"])
async def request_consent(
    payload: ConsentRequest,
    masked_internal_id: str = Depends(get_scoped_session),
) -> dict:
    """Issues a time-bound consent token stored in Upstash Redis.

    masked_internal_id comes from the caller's own handshake session, not
    the request body -- this was previously the open door in the system:
    anyone could mint a consent token for any patient with no auth at all.
    A valid session now proves who the caller is authenticated as before
    a token is ever issued for them.
    """
    consent_token = issue_token(
        masked_internal_id=masked_internal_id,
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