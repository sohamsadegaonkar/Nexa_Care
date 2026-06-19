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

  F-17 — Replaced every dead `getattr(response, "error", None)` check
          with try/except around the Supabase call chain. In supabase-py
          2.x, PostgREST errors (4xx/5xx) raise postgrest.APIError; they
          do NOT populate a truthy `.error` attribute on a returned
          result object (that was 1.x behavior). The old checks were
          therefore unreachable on the failure path in every route here
          — a real DB failure on /register, /api/v1/record, or
          /view-record instead propagated as an unhandled APIError
          straight to GlobalLoggingMiddleware, which masks it as a
          generic DB_CONNECTION_LOST 503 with NO audit log entry for the
          failure, breaking the "audit precedes/accompanies every state
          change" guarantee on every DB-error path.

          This mirrors the fix already applied to
          app/services/biometric_registry.py (see its F-15/F-16 comments)
          — same root cause, same fix shape, ported here so the audit
          trail no longer has a gap on DB failures in these three routes.
"""
from __future__ import annotations

import json
import logging

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

logger = logging.getLogger("nexa_logger")          # F-17

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

    F-17: the two .insert().execute() calls are now wrapped in their own
    try/except. In supabase-py 2.x a PostgREST rejection (RLS denial,
    constraint violation, connection failure, etc.) raises APIError
    directly out of execute() — the previous
    `getattr(vault_response, "error", None)` check could never observe
    that failure, because the line itself would have raised before being
    reached. Catching it explicitly here means the existing
    PATIENT_REGISTRATION_FAILED audit entry (in the outer except block)
    now actually fires for DB-layer failures, not just for failures in
    the surrounding Python logic.
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

        # F-17: APIError raised by either insert is caught here, logged
        # with the table that failed, and re-raised as a 502 so it is
        # handled the same way (HTTPException, not a bare 500) regardless
        # of which shard write failed. This also ensures the outer
        # except block's PATIENT_REGISTRATION_FAILED audit entry runs.
        try:
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
        except Exception as exc:
            logger.critical(json.dumps({
                "event": "patient_registration_db_error",
                "shard": "nexa_vault",
                "masked_internal_id": str(masked_internal_id),
                "exception": str(exc),
            }))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to write PII vault record.",
            ) from exc

        try:
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
        except Exception as exc:
            logger.critical(json.dumps({
                "event": "patient_registration_db_error",
                "shard": "nexa_clinical",
                "masked_internal_id": str(masked_internal_id),
                "exception": str(exc),
            }))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to write clinical record.",
            ) from exc

        # F-17: retained as belt-and-suspenders for any future supabase-py
        # version that reverts to 1.x-style error surfacing, and for test
        # doubles that mock a truthy `.error` instead of raising. Dead in
        # production today, but cheap and harmless to keep as a second
        # line of defense — same rationale as biometric_registry.py F-16.
        if getattr(vault_response, "error", None) or getattr(clinical_response, "error", None):
            raise RuntimeError("Supabase insert failed during patient registration")

        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_SUCCESS",
            target_id=str(masked_internal_id),
            status="SUCCESS",
        )

        return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)

    except HTTPException:
        await append_audit_log(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_FAILED",
            target_id="FAILED_GENERATION",
            status="CRITICAL_ERROR",
        )
        raise
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

    F-17: both .select().execute() calls are wrapped in try/except. A
    PostgREST APIError here previously skipped the
    `getattr(..., "error", None)` check entirely (it never executes,
    because the exception is raised before the assignment completes) and
    propagated unhandled past the RECORD_ACCESS_ATTEMPT audit entry with
    no matching failure entry — the only sign anything happened was a
    generic 503 from the global error handler. Now a DB failure logs a
    RECORD_ACCESS_FAILED audit entry and returns a proper 502, while a
    genuinely missing record (no exception, just empty data) still
    returns 404 as before.
    """
    await append_audit_log(
        actor_uid="REASSEMBLY_ENGINE",
        event_type="RECORD_ACCESS_ATTEMPT",
        target_id=masked_internal_id,
        status="STARTED",
    )

    supabase = get_supabase_client()

    try:
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
    except Exception as exc:
        # .single() raises APIError both on a genuine DB failure and on
        # "0 rows" (PGRST116) -- distinguish them so a missing record
        # still surfaces as 404, not a CRITICAL-logged 502.
        exc_str = str(exc)
        if "PGRST116" in exc_str or "JSON object requested, multiple (or no) rows returned" in exc_str:
            await append_audit_log(
                actor_uid="REASSEMBLY_ENGINE",
                event_type="RECORD_ACCESS_FAILED",
                target_id=masked_internal_id,
                status="NOT_FOUND",
            )
            raise HTTPException(status_code=404, detail="Record not found") from exc

        logger.critical(json.dumps({
            "event": "record_reassembly_db_error",
            "masked_internal_id": masked_internal_id,
            "exception": exc_str,
        }))
        await append_audit_log(
            actor_uid="REASSEMBLY_ENGINE",
            event_type="RECORD_ACCESS_FAILED",
            target_id=masked_internal_id,
            status="DB_ERROR",
        )
        raise HTTPException(status_code=502, detail="Record reassembly failed") from exc

    # F-17: retained as belt-and-suspenders (see register_patient above).
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

    F-17: both .select().execute() calls wrapped in try/except for the
    same reason as get_record() above — a PostgREST APIError previously
    bypassed the `getattr(..., "error", None)` check entirely (unreachable
    dead code on the failure path) and propagated unhandled to the global
    handler as a generic 503, with no audit trail and no distinction from
    a transient DB outage. Note this route had NO audit logging at all
    before this fix (unlike get_record/register_patient) -- it now gets a
    minimal VIEW_RECORD_FAILED entry on DB error so a real outage here is
    no longer silent in the ledger either.
    """
    consent_token = consent_token_header or consent_token_query
    masked_internal_id = validate_token(consent_token) if consent_token else None

    if masked_internal_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    supabase = get_supabase_client()

    try:
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
    except Exception as exc:
        logger.critical(json.dumps({
            "event": "view_record_db_error",
            "masked_internal_id": masked_internal_id,
            "exception": str(exc),
        }))
        await append_audit_log(
            actor_uid="CONSENT_VIEWER",
            event_type="VIEW_RECORD_FAILED",
            target_id=masked_internal_id,
            status="DB_ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve record.",
        ) from exc

    # F-17: retained as belt-and-suspenders (see register_patient above).
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