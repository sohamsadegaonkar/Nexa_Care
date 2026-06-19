"""API router for Nexa Care endpoints.

Fixes applied in this file:
  F-10 — GET /view-record now applies redact_payload() to the PII fields
          before returning them.  The previous implementation returned
          patient_name and phone in plaintext.  The consent-token gate
          proves the caller is authorised to VIEW the record; it does not
          mean raw PII should be transmitted unredacted over the wire.
          If a downstream consumer (e.g. a kiosk display) genuinely needs
          the unmasked value it must call a separate, more tightly audited
          endpoint — this one should never have been a raw-PII pipe.

  F-09 note — the dual write-shape (OCR path: raw_pii / clinical_data
          blobs vs registration path: flat columns) is preserved here
          because unifying the DB schema is a data-migration task that
          must be done with a Supabase migration, not just a code change.
          The reassembly engine's dual-read branch is kept as-is and
          marked with a TODO so the next schema migration removes it.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.dependencies import get_scoped_session, verify_provider_token
from app.core.redis import issue_token, validate_token
from app.core.supabase import get_supabase_client
from app.observability.audit_ledger import append_audit_log
from app.observability.redactor import redact_payload           # F-10
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
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)


class HandshakePayload(BaseModel):
    nfc_uid: str
    bio_seed: str
    masked_internal_id: UUID


class EnrollBiometricPayload(BaseModel):
    masked_internal_id: UUID
    nfc_uid: str
    bio_seed: str


@router.post("/api/v1/handshake", tags=["auth"])
async def process_handshake(payload: HandshakePayload):
    """Biometric handshake: verifies the enrolled (nfc_uid, bio_seed) pair
    for the claimed patient, then mints a scoped session token.

    F-03 enforcement lives inside process_biometric_handshake() —
    verify_biometric_binding() is called before any derivation runs, so an
    unenrolled pair receives a 400 here, not a valid session.
    """
    auth_result = await process_biometric_handshake(
        nfc_uid=payload.nfc_uid,
        bio_seed=payload.bio_seed,
        masked_internal_id=str(payload.masked_internal_id),
    )

    if auth_result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Biometric verification failed or binding not enrolled.",
        )

    return auth_result


@router.post("/api/v1/enroll-biometric", tags=["auth"], status_code=status.HTTP_201_CREATED)
async def enroll_biometric(
    payload: EnrollBiometricPayload,
    _: None = Depends(verify_provider_token),
):
    """Provider-only: binds an (nfc_uid, bio_seed) pair to a patient.
    Gated behind verify_provider_token — a patient cannot self-enroll.
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
    """Register a patient: write PII to nexa_vault, clinical data to
    nexa_clinical, under a shared masked_internal_id.
    Gated behind verify_provider_token.
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
            .insert({
                "masked_internal_id": str(masked_internal_id),
                "patient_name": pii_vault.patient_name,
                "phone": pii_vault.phone,
                "aadhaar_abha_id": pii_vault.aadhaar_abha_id,
            })
            .execute()
        )

        clinical_response = (
            supabase.table("nexa_clinical")
            .insert({
                "masked_internal_id": str(masked_internal_id),
                "diagnoses": clinical_record.diagnoses,
                "lab_results": clinical_record.lab_results,
                "prescriptions": clinical_record.prescriptions,
            })
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
    """Reassembly engine.  masked_internal_id comes only from the session
    (bound at handshake time) — never from a URL param — closing the IDOR.

    TODO (F-09): remove the dual-read branch once the DB schema is unified
    on flat columns via a Supabase migration.
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

    # TODO (F-09): remove dual-branch once DB schema is unified
    pii_payload = (vault_data.get("raw_pii") if "raw_pii" in vault_data else vault_data) or {}
    clinical_payload = (
        clinical_data.get("clinical_data") if "clinical_data" in clinical_data else clinical_data
    ) or {}

    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_COMPLETED",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    return {
        "masked_internal_id": masked_internal_id,
        "pii": pii_payload,
        "clinical": clinical_payload,
    }


@router.post("/request-consent", tags=["consent"])
async def request_consent(
    payload: ConsentRequest,
    masked_internal_id: str = Depends(get_scoped_session),
) -> dict:
    """Issue a time-bound consent token.  masked_internal_id comes from the
    caller's handshake session, not the request body.
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
    """Zero-trust retrieval via consent token.

    F-10 fix: PII fields are passed through redact_payload() before being
    returned.  The consent-token gate proves the caller is authorised to
    view this record; it does not authorise receiving raw PII over the
    wire.  A kiosk display that genuinely needs the unmasked value must
    call a separate, purpose-specific, audit-logged endpoint.
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

    # F-10: redact PII fields before returning.  patient_name and phone are
    # both in SENSITIVE_FIELDS, so they become "[REDACTED]" here.
    raw_response = {
        "masked_internal_id": masked_internal_id,
        "patient_name": vault.get("patient_name"),
        "phone": vault.get("phone"),
        "diagnoses": clinical.get("diagnoses"),
        "lab_results": clinical.get("lab_results"),
        "prescriptions": clinical.get("prescriptions"),
    }
    return redact_payload(raw_response)
