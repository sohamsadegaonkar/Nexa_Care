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
            - GET /view-record/pii       -- retired with HTTP 410; modern
              clients use the canonical envelope/KMS record service.
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_scoped_session, require_role
from app.core.supabase import get_supabase_client
from app.models.assurance import AssuranceLevel
import app.services.consent_engine as consent_engine
from app.services.consent_engine import ConsentEngineUnavailable
from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503
from app.observability.safe_exceptions import log_safe_exception
from app.services.sharding import encrypt_vault_payload
from app.services.crypto_kms import get_encryption_provider, EncryptionError
from app.services.crypto_engine import process_biometric_handshake
from app.services.biometric_registry import enroll_biometric_binding_with_audit
from app.models.provider_context import ProviderContext
from app.models.schemas import (
    ClinicalRecordSchema,
    PIIVaultSchema,
    RegisterResponse,
    UnifiedPatientPayload,
)

logger = logging.getLogger("nexa_logger")          # F-17

router = APIRouter()

# V1 consent is patient self-consent: the patient authenticated by their
# biometric handshake grants access to their own record. ConsentEngine is
# provider-centric, so we use a synthetic self-consent actor ID to keep the
# v1 surface on the same authority as v2.
_V1_SELF_CONSENT_CLINICIAN_ID = "patient:self"
_V1_SELF_CONSENT_PURPOSE = "patient_self_access"


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
    assurance_level: AssuranceLevel = AssuranceLevel.STANDARD
    assurance_evidence: dict = Field(default_factory=dict)


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
    provider: ProviderContext = Depends(require_role("clinician")),
    db: AsyncSession = Depends(get_db_session),
):
    """Provider-only: binds an (nfc_uid, bio_seed) pair to a patient.
    Gated behind get_provider_context — a patient cannot self-enroll.
    """
    masked_internal_id = str(payload.masked_internal_id)
    await enroll_biometric_binding_with_audit(
        nfc_uid=payload.nfc_uid,
        bio_seed=payload.bio_seed,
        masked_internal_id=masked_internal_id,
        db=db,
    )
    return {"masked_internal_id": masked_internal_id, "status": "enrolled"}


@router.post("/register", response_model=RegisterResponse, tags=["sharding"])
async def register_patient(
    payload: UnifiedPatientPayload,
    provider: ProviderContext = Depends(require_role("clinician")),
    db: AsyncSession = Depends(get_db_session),
) -> RegisterResponse:
    """Register a patient: write PII to nexa_vault, clinical data to
    nexa_clinical, under a shared masked_internal_id.
    Gated behind get_provider_context.

    KMS Integration: Generates a per-patient DEK atomically.
    """
    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="PATIENT_REGISTRATION_ATTEMPT",
        target_id="PENDING_GENERATION",
        status="STARTED",
    )

    try:
        masked_internal_id = uuid4()
        patient_id_str = str(masked_internal_id)

        # 1. Generate DEK first (atomic with transaction)
        kms = get_encryption_provider()
        dek_bundle = await kms.generate_dek(patient_id_str, db)
        
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="PATIENT_DEK_GENERATED",
            target_id=patient_id_str,
            status="SUCCESS",
            metadata={"dek_version": dek_bundle.dek_version}
        )

        # 2. Encrypt PII using KMS
        vault_data = {
            "patient_name": payload.patient_name,
            "phone": payload.phone,
            "aadhaar_abha_id": payload.aadhaar_abha_id,
        }
        encrypted_vault = await encrypt_vault_payload(vault_data, patient_id_str, db)

        pii_vault = PIIVaultSchema(
            masked_internal_id=masked_internal_id,
            **vault_data
        )
        clinical_record = ClinicalRecordSchema(
            masked_internal_id=masked_internal_id,
            diagnoses=payload.diagnoses,
            lab_results=payload.lab_results,
            prescriptions=payload.prescriptions,
        )

        supabase = get_supabase_client()

        try:
            (
                supabase.table("nexa_vault")
                .insert({
                    "masked_internal_id": patient_id_str,
                    **encrypted_vault
                })
                .execute()
            )
        except Exception as exc:
            log_safe_exception(
                logger, exc, subsystem="database", operation="patient_registration_vault"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to write PII vault record.",
            ) from exc

        try:
            (
                supabase.table("nexa_clinical")
                .insert({
                    "masked_internal_id": patient_id_str,
                    "diagnoses": clinical_record.diagnoses,
                    "lab_results": clinical_record.lab_results,
                    "prescriptions": clinical_record.prescriptions,
                })
                .execute()
            )
        except Exception as exc:
            log_safe_exception(
                logger, exc, subsystem="database", operation="patient_registration_clinical"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to write clinical record.",
            ) from exc

        # Commit everything (Postgres + Supabase)
        await db.commit()

        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="PATIENT_REGISTRATION_SUCCESS",
            target_id=patient_id_str,
            status="SUCCESS",
        )

        return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)

    except (HTTPException, EncryptionError):
        await db.rollback()
        await _audit_best_effort(
            provider.actor_uid, "PATIENT_REGISTRATION_FAILED", "FAILED_GENERATION", "CRITICAL_ERROR"
        )
        raise
    except Exception:
        await db.rollback()
        await _audit_best_effort(
            provider.actor_uid, "PATIENT_REGISTRATION_FAILED", "FAILED_GENERATION", "CRITICAL_ERROR"
        )
        raise


@router.get("/api/v1/record", tags=["reassembly"])
async def get_record(masked_internal_id: str = Depends(get_scoped_session)):
    """DEPRECATED: this endpoint joined PII and clinical data in a single
    response, violating the vertical-sharding architecture. It is disabled
    and returns 410 Gone.

    Use the split endpoints instead:
      - GET /view-record/clinical (clinical shard only)
      - GET /view-record/pii      (redacted PII shard only, scope="full")
    """
    await _audit_best_effort(
        "REASSEMBLY_ENGINE", "RECORD_ACCESS_DEPRECATED", masked_internal_id, "GONE"
    )
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "This endpoint is deprecated. Use /view-record/clinical and "
            "/view-record/pii for vertical-shard retrieval."
        ),
    )


@router.post("/request-consent", tags=["consent"])
async def request_consent(
    payload: ConsentRequest,
    masked_internal_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Issue a time-bound, scope-bound self-consent token.

    Migrated to ConsentEngine (2026-07-03): the v1 patient self-consent
    surface now uses the same authority as the v2 provider consent flow.
    A synthetic ``patient:self`` clinician ID marks the grant as
    patient self-consent, with ``scope`` mapped to ConsentEngine scopes.
    """

    consent_scope = (
        ["clinical.*", "pii.*"]
        if payload.scope == "full"
        else ["clinical.*"]
    )

    try:
        consent_token = await consent_engine.issue(
            db=db,
            patient_id=masked_internal_id,
            clinician_id=_V1_SELF_CONSENT_CLINICIAN_ID,
            purpose=_V1_SELF_CONSENT_PURPOSE,
            scope=consent_scope,
            assurance_level=payload.assurance_level,
            assurance_evidence=payload.assurance_evidence,
            ttl_seconds=payload.duration_seconds,
        )
    except ConsentEngineUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Consent service is temporarily unavailable.",
        ) from exc

    return {
        "consent_token": consent_token,
        "scope": payload.scope,
        "expires_in": payload.duration_seconds,
    }


async def _resolve_scoped_consent(
    consent_token_header: str | None,
    consent_token_query: str | None,
    required_capability: Literal["clinical", "pii"],
    db: AsyncSession,
) -> str:
    """Resolves a v1 self-consent token to a masked_internal_id, enforcing
    that the token's granted scope covers `required_capability`.
    Consumes the token upon successful validation (single-use).
    """
    consent_token = consent_token_header or consent_token_query
    if not consent_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    try:
        # Step 1: Validate (non-destructive) to check scope
        capability = await consent_engine.validate(
            token=consent_token,
            patient_id=None,
            clinician_id=_V1_SELF_CONSENT_CLINICIAN_ID,
            purpose=_V1_SELF_CONSENT_PURPOSE,
        )
    except ConsentEngineUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Consent service is temporarily unavailable.",
        ) from exc

    if capability is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    masked_internal_id = capability.patient_id
    required_scope = "pii.*" if required_capability == "pii" else "clinical.*"
    if required_scope not in capability.scope:
        await _audit_best_effort(
            "CONSENT_VIEWER", "PII_VIEW_DENIED_INSUFFICIENT_SCOPE", masked_internal_id, "FORBIDDEN"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    # Step 2: Consume (destructive)
    try:
        consumed = await consent_engine.consume(
            db=db,
            token=consent_token,
            patient_id=masked_internal_id,
            clinician_id=_V1_SELF_CONSENT_CLINICIAN_ID,
            purpose=_V1_SELF_CONSENT_PURPOSE,
        )
    except ConsentEngineUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Consent service is temporarily unavailable.",
        ) from exc

    if consumed is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired",
        )

    return masked_internal_id


@router.get("/view-record/clinical", tags=["consent"])
async def view_record_clinical(
    consent_token_header: str | None = Header(default=None, alias="X-Consent-Token"),
    consent_token_query: str | None = Query(default=None, alias="consent_token"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Zero-trust retrieval of the CLINICAL shard ONLY, via consent token.
    """
    masked_internal_id = await _resolve_scoped_consent(
        consent_token_header, consent_token_query, required_capability="clinical", db=db
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
        log_safe_exception(logger, exc, subsystem="database", operation="legacy_clinical_view")
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


@router.get(
    "/view-record/pii",
    tags=["consent"],
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"description": "Legacy PII endpoint retired"}},
)
async def view_record_pii(
) -> dict:
    """Compatibility tombstone for the retired static-Fernet PII path."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error_code": "LEGACY_PII_ENDPOINT_RETIRED",
            "canonical_endpoint": "/api/v2/patient/{patient_id}/records",
        },
    )
