"""API router for Nexa Care endpoints.

Fixes applied in this file (prior revisions):
  F-10 — PII fields are redact_payload()'d before being returned from any
          consent-token-gated view route.
  F-09 note — the dual write-shape (OCR path: raw_pii / clinical_data
          blobs vs registration path: flat columns) is preserved here
          because unifying the DB schema is a data-migration task that
          must be done with a Supabase migration, not just a code change.
          The reassembly engine's dual-read branch is kept as-is and
          marked with a TODO so the next schema migration removes it.
  F-17 — Every Supabase call chain is wrapped in try/except instead of
          relying on the dead `getattr(response, "error", None)` check
          (supabase-py 2.x raises APIError; it never populates a truthy
          `.error` attribute). The dead checks are retained as
          belt-and-suspenders comments where they were already present.

Fixes applied in THIS revision
-------------------------------
SHARD-SEPARATION FIX — GET /view-record has been removed. It returned
          PII (redacted) and clinical data together in a single response,
          which contradicted the vertical-sharding architecture even
          though the PII came back redacted: a single endpoint should
          never be the join point for both shards. It is replaced by two
          endpoints that each read exactly one shard:
            - GET /view-record/clinical  -- de-identified clinical data only.
            - GET /view-record/pii       -- redacted PII only, and only
              honored for a consent token whose scope is "full".
          /request-consent now takes an explicit `scope` field
          ("clinical" default, or "full") and issues a token bound to
          that scope via app.core.redis.issue_consent_token(). See that
          module's docstring for the token format.

AUDIT-CONSISTENCY FIX — the architecture's stated rule is "if the audit
          log fails, the route MUST raise HTTPException(503) and abort."
          Before this revision, every route in this file except the
          biometric-enrollment path (app/services/biometric_registry.py)
          used the fire-and-forget append_audit_log() for EVERY call,
          including pre-condition and success-path audits, and never
          checked the return value. Concretely:
            - register_patient fired DB writes regardless of whether the
              ATTEMPT log succeeded, and returned 200 regardless of
              whether the SUCCESS log succeeded.
            - get_record returned PII+clinical data regardless of whether
              RECORD_ACCESS_COMPLETED was actually written.
            - request_consent had NO audit logging at all -- minting a
              consent token left zero ledger trace, success or failure.
            - view_record (now split) only ever logged failures, never a
              successful PII/clinical view.
          This revision uses append_audit_log_or_503() for every
          pre-condition and success-path audit (ATTEMPT, SUCCESS,
          COMPLETED, ISSUED, CLINICAL_VIEW_SUCCESS, PII_VIEW_SUCCESS) and
          a new _audit_best_effort() helper for failure/denial-path
          logging, where hard-failing would risk replacing a real error
          with an unrelated 503. request_consent now audits both the
          attempt and the issuance, and rolls back (revokes) a token that
          was minted but couldn't be proven audited.
"""
from __future__ import annotations

import json
import logging

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.dependencies import get_scoped_session, verify_provider_token
from app.core.redis import issue_consent_token, resolve_consent_token, revoke_consent_token
from app.core.supabase import get_supabase_client
from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503
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


async def _audit_best_effort(actor_uid: str, event_type: str, target_id: str, status_: str) -> None:
    """Best-effort audit write for failure/denial-path logging.

    Hard-failing (append_audit_log_or_503) is correct for pre-condition
    and success-path audits elsewhere in this file -- an action whose
    audit entry can't be written must not be allowed to proceed or to be
    reported as successful. It would be WRONG here: these calls run
    inside an `except` block (or right before raising a 404/403) where a
    *real* failure is already being reported to the caller. Raising a
    fresh HTTPException(503) from inside that path would silently
    replace the true error with an unrelated one. So this logs loudly on
    failure instead of raising.
    """
    success = await append_audit_log(
        actor_uid=actor_uid, event_type=event_type, target_id=target_id, status=status_,
    )
    if not success:
        logger.critical(json.dumps({
            "event": "audit_log_write_failed_best_effort",
            "context": event_type,
            "target_id": target_id,
        }))


class ConsentRequest(BaseModel):
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)
    scope: Literal["clinical", "full"] = Field(
        default="clinical",
        description=(
            "Which shard(s) this token authorizes. 'clinical' (default) "
            "grants only GET /view-record/clinical -- the data-minimizing "
            "default. 'full' additionally grants GET /view-record/pii and "
            "must be explicitly requested."
        ),
    )


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
    unenrolled pair receives a 401 here, not a valid session. A 503 from
    that function (audit write failed on the success path) propagates
    here unchanged, distinct from the 401 below.
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

    AUDIT-CONSISTENCY FIX: ATTEMPT and SUCCESS now use
    append_audit_log_or_503 -- a registration whose audit trail can't be
    written is no longer allowed to silently proceed (ATTEMPT) or to
    silently report success to the caller (SUCCESS). FAILED logging
    stays best-effort (_audit_best_effort) so an audit-ledger outage
    encountered while we are already failing doesn't replace the real
    error with an unrelated 503.
    """
    await append_audit_log_or_503(
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

        await append_audit_log_or_503(
            actor_uid="SYSTEM_INGEST",
            event_type="PATIENT_REGISTRATION_SUCCESS",
            target_id=str(masked_internal_id),
            status="SUCCESS",
        )

        return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)

    except HTTPException:
        await _audit_best_effort(
            "SYSTEM_INGEST", "PATIENT_REGISTRATION_FAILED", "FAILED_GENERATION", "CRITICAL_ERROR"
        )
        raise
    except Exception:
        await _audit_best_effort(
            "SYSTEM_INGEST", "PATIENT_REGISTRATION_FAILED", "FAILED_GENERATION", "CRITICAL_ERROR"
        )
        raise


@router.get("/api/v1/record", tags=["reassembly"])
async def get_record(masked_internal_id: str = Depends(get_scoped_session)):
    """Reassembly engine.  masked_internal_id comes only from the session
    (bound at handshake time) — never from a URL param — closing the IDOR.

    TODO (F-09): remove the dual-read branch once the DB schema is unified
    on flat columns via a Supabase migration.

    AUDIT-CONSISTENCY FIX: ATTEMPT and COMPLETED now use
    append_audit_log_or_503; every failure/not-found branch uses the
    best-effort helper so a DB outage or audit-write failure on the
    failure path doesn't replace the real 404/502 being returned.
    """
    await append_audit_log_or_503(
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
            await _audit_best_effort(
                "REASSEMBLY_ENGINE", "RECORD_ACCESS_FAILED", masked_internal_id, "NOT_FOUND"
            )
            raise HTTPException(status_code=404, detail="Record not found") from exc

        logger.critical(json.dumps({
            "event": "record_reassembly_db_error",
            "masked_internal_id": masked_internal_id,
            "exception": exc_str,
        }))
        await _audit_best_effort(
            "REASSEMBLY_ENGINE", "RECORD_ACCESS_FAILED", masked_internal_id, "DB_ERROR"
        )
        raise HTTPException(status_code=502, detail="Record reassembly failed") from exc

    # F-17: retained as belt-and-suspenders (see register_patient above).
    if getattr(vault_response, "error", None) or getattr(clinical_response, "error", None):
        await _audit_best_effort(
            "REASSEMBLY_ENGINE", "RECORD_ACCESS_FAILED", masked_internal_id, "NOT_FOUND"
        )
        raise HTTPException(status_code=404, detail="Record not found")

    vault_data = getattr(vault_response, "data", None)
    clinical_data = getattr(clinical_response, "data", None)

    if not vault_data or not clinical_data:
        await _audit_best_effort(
            "REASSEMBLY_ENGINE", "RECORD_ACCESS_FAILED", masked_internal_id, "NOT_FOUND"
        )
        raise HTTPException(status_code=404, detail="Record not found")

    # TODO (F-09): remove dual-branch once DB schema is unified
    pii_payload = (vault_data.get("raw_pii") if "raw_pii" in vault_data else vault_data) or {}
    clinical_payload = (
        clinical_data.get("clinical_data") if "clinical_data" in clinical_data else clinical_data
    ) or {}

    # Hard-fail: a successful reassembly that can't be proven in the
    # ledger must not be returned to the caller.
    await append_audit_log_or_503(
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
    """Issue a time-bound, scope-bound consent token.  masked_internal_id
    comes from the caller's handshake session, not the request body.

    SHARD-SEPARATION FIX: `scope` selects which of the two split
    view-record endpoints the resulting token can be used against. See
    app.core.redis's module docstring for the token format.

    AUDIT-CONSISTENCY FIX: this route previously had NO audit logging at
    all. Both the attempt and the successful issuance are now hard-
    audited via append_audit_log_or_503. If the post-issuance audit
    write fails, the just-minted token is revoked before the 503
    propagates, so an unauditable grant of access never remains valid.
    """
    await append_audit_log_or_503(
        actor_uid="CONSENT_ISSUER",
        event_type="CONSENT_TOKEN_ISSUE_ATTEMPT",
        target_id=masked_internal_id,
        status=f"STARTED_SCOPE_{payload.scope.upper()}",
    )

    consent_token = issue_consent_token(
        masked_internal_id=masked_internal_id,
        scope=payload.scope,
        ttl_seconds=payload.duration_seconds,
    )

    try:
        await append_audit_log_or_503(
            actor_uid="CONSENT_ISSUER",
            event_type="CONSENT_TOKEN_ISSUED",
            target_id=masked_internal_id,
            status=f"SUCCESS_SCOPE_{payload.scope.upper()}",
        )
    except HTTPException:
        # The token already exists in Redis but we couldn't prove we
        # logged its issuance. Best-effort revoke, then re-raise the
        # original 503 regardless of whether the revoke itself succeeds.
        revoke_consent_token(consent_token)
        raise

    return {
        "consent_token": consent_token,
        "scope": payload.scope,
        "expires_in": payload.duration_seconds,
    }


async def _resolve_scoped_consent(
    consent_token_header: str | None,
    consent_token_query: str | None,
    required_capability: Literal["clinical", "pii"],
) -> str:
    """Resolves a consent token to a masked_internal_id, enforcing that
    the token's granted scope covers `required_capability`.

    Granted scope "full" covers both capabilities; granted scope
    "clinical" covers only the "clinical" capability. A token that's
    simply invalid/expired and a token that's valid-but-under-scoped both
    come back as a 403 with the same message -- the caller doesn't get
    to distinguish "wrong token" from "right token, wrong privilege" by
    the response shape.
    """
    consent_token = consent_token_header or consent_token_query
    resolved = resolve_consent_token(consent_token) if consent_token else None

    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    masked_internal_id = resolved["masked_internal_id"]
    granted_scope = resolved["scope"]

    if required_capability == "pii" and granted_scope != "full":
        await _audit_best_effort(
            "CONSENT_VIEWER", "PII_VIEW_DENIED_INSUFFICIENT_SCOPE", masked_internal_id, "FORBIDDEN"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    return masked_internal_id


@router.get("/view-record/clinical", tags=["consent"])
async def view_record_clinical(
    consent_token_header: str | None = Header(default=None, alias="X-Consent-Token"),
    consent_token_query: str | None = Query(default=None, alias="consent_token"),
) -> dict:
    """Zero-trust retrieval of the CLINICAL shard ONLY, via consent token.

    SHARD-SEPARATION FIX: replaces the old combined GET /view-record.
    This endpoint reads exclusively from nexa_clinical and never touches
    nexa_vault. Available under either consent scope ("clinical" or
    "full"), since clinical-only access is the data-minimizing default
    /request-consent now issues unless the caller explicitly asks for
    scope="full".

    AUDIT-CONSISTENCY FIX: a successful read now hard-audits
    CLINICAL_VIEW_SUCCESS (append_audit_log_or_503) BEFORE the response
    is returned -- the prior combined endpoint had no success-path audit
    logging at all, only a failure-path entry. Failure/not-found logging
    stays best-effort so a DB error isn't itself masked by a second
    failure.
    """
    masked_internal_id = await _resolve_scoped_consent(
        consent_token_header, consent_token_query, required_capability="clinical"
    )

    supabase = get_supabase_client()

    try:
        clinical_res = (
            supabase.table("nexa_clinical")
            .select("diagnoses,lab_results,prescriptions,masked_internal_id")
            .eq("masked_internal_id", masked_internal_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.critical(json.dumps({
            "event": "clinical_view_db_error",
            "masked_internal_id": masked_internal_id,
            "exception": str(exc),
        }))
        await _audit_best_effort(
            "CONSENT_VIEWER", "CLINICAL_VIEW_FAILED", masked_internal_id, "DB_ERROR"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve clinical record.",
        ) from exc

    # F-17: retained as belt-and-suspenders (see register_patient above).
    if getattr(clinical_res, "error", None):
        await _audit_best_effort(
            "CONSENT_VIEWER", "CLINICAL_VIEW_FAILED", masked_internal_id, "DB_ERROR"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve clinical record.",
        )

    clinical_rows = getattr(clinical_res, "data", None) or []
    if not clinical_rows:
        await _audit_best_effort(
            "CONSENT_VIEWER", "CLINICAL_VIEW_FAILED", masked_internal_id, "NOT_FOUND"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Record not found for masked_internal_id",
        )

    clinical = clinical_rows[0]

    await append_audit_log_or_503(
        actor_uid="CONSENT_VIEWER",
        event_type="CLINICAL_VIEW_SUCCESS",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    return {
        "masked_internal_id": masked_internal_id,
        "diagnoses": clinical.get("diagnoses"),
        "lab_results": clinical.get("lab_results"),
        "prescriptions": clinical.get("prescriptions"),
    }


@router.get("/view-record/pii", tags=["consent"])
async def view_record_pii(
    consent_token_header: str | None = Header(default=None, alias="X-Consent-Token"),
    consent_token_query: str | None = Query(default=None, alias="consent_token"),
) -> dict:
    """Zero-trust retrieval of the (redacted) PII shard ONLY, via a
    consent token explicitly issued with scope="full".

    SHARD-SEPARATION FIX: replaces the old combined GET /view-record.
    A "clinical"-scope token (the /request-consent default) is rejected
    here with the same 403 used for an invalid token -- see
    _resolve_scoped_consent's docstring for why the response doesn't
    distinguish the two cases.

    F-10 still applies: a "full"-scope consent token proves the caller is
    authorised to receive the redacted PII shard; it does not authorise
    raw, unmasked PII over the wire. Nothing in this codebase currently
    unmasks PII for any caller -- that would be a separate, more tightly
    audited, purpose-built capability, not a flag on this endpoint.

    AUDIT-CONSISTENCY FIX: same shape as view_record_clinical above --
    PII_VIEW_SUCCESS is hard-audited before the response is returned;
    PII_VIEW_FAILED / PII_VIEW_DENIED_INSUFFICIENT_SCOPE are best-effort.
    """
    masked_internal_id = await _resolve_scoped_consent(
        consent_token_header, consent_token_query, required_capability="pii"
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
    except Exception as exc:
        logger.critical(json.dumps({
            "event": "pii_view_db_error",
            "masked_internal_id": masked_internal_id,
            "exception": str(exc),
        }))
        await _audit_best_effort(
            "CONSENT_VIEWER", "PII_VIEW_FAILED", masked_internal_id, "DB_ERROR"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve PII record.",
        ) from exc

    # F-17: retained as belt-and-suspenders (see register_patient above).
    if getattr(vault_res, "error", None):
        await _audit_best_effort(
            "CONSENT_VIEWER", "PII_VIEW_FAILED", masked_internal_id, "DB_ERROR"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve PII record.",
        )

    vault_rows = getattr(vault_res, "data", None) or []
    if not vault_rows:
        await _audit_best_effort(
            "CONSENT_VIEWER", "PII_VIEW_FAILED", masked_internal_id, "NOT_FOUND"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Record not found for masked_internal_id",
        )

    vault = vault_rows[0]

    await append_audit_log_or_503(
        actor_uid="CONSENT_VIEWER",
        event_type="PII_VIEW_SUCCESS",
        target_id=masked_internal_id,
        status="SUCCESS",
    )

    # F-10: redact PII fields before returning.  patient_name and phone are
    # both in SENSITIVE_FIELDS, so they become "[REDACTED]" here.
    raw_response = {
        "masked_internal_id": masked_internal_id,
        "patient_name": vault.get("patient_name"),
        "phone": vault.get("phone"),
    }
    return redact_payload(raw_response)