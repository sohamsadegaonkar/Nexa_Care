"""Consent-scoped patient reconstruction routes for Nexa Care V2."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    require_clinical_capability,
    require_role,
)
from app.security.provider_capabilities import ClinicalCapability
from app.models.provider_context import ProviderContext
from app.models.shards import NexaClinical, NexaVault
import app.services.consent_engine as consent_engine
from app.services.sharding import decrypt_vault_field
from app.services.consent_gated_crypto import consent_gated_decrypt, EncryptionProvider
from app.services.consent_engine import get_consent_redis_client
from app.services.crypto_kms import get_encryption_provider
from app.services.emergency_summary_service import build_emergency_summary
from app.security.clinical_categories import (
    UnsupportedClinicalCategoryError,
    parse_clinical_categories,
)
from app.observability.audit_ledger import append_audit_log_or_503
from app.observability.safe_exceptions import log_safe_exception
from app.security.audit_context import AuditDomain, current_audit_context

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/patient", tags=["patient"])


async def get_kms_provider() -> EncryptionProvider:
    """Resolve the same configured envelope-encryption provider used elsewhere."""
    return get_encryption_provider()


class ErasureRequest(BaseModel):
    confirmation: str
    reason: str


class ErasureResponse(BaseModel):
    status: str
    patient_id: str
    assurance_level: str
    wrapping_key_type: str
    operator_action_required: bool
    historical_backup_irrecoverability_proven: bool


def _merge_non_null_fields(
    base: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    """Return a copy containing only non-null shard fields."""

    merged = dict(base)
    for key, value in values.items():
        if value is not None:
            merged[key] = value
    return merged


def _pii_payload(row: NexaVault) -> dict[str, Any]:
    """Build an identity-only payload without clinical fields."""

    return _merge_non_null_fields(
        {},
        {
            "patient_name": row.patient_name,
            "phone": row.phone,
            "aadhaar_abha_id": row.aadhaar_abha_id,
        },
    )


def _clinical_payload(row: NexaClinical) -> dict[str, Any]:
    """Build a clinical-only payload without identity fields."""

    clinical_data = row.clinical_data if isinstance(row.clinical_data, dict) else {}
    return _merge_non_null_fields(
        clinical_data,
        {
            "diagnoses": row.diagnoses,
            "lab_results": row.lab_results,
            "prescriptions": row.prescriptions,
        },
    )


async def _fetch_pii_shard(patient_id: str, db: AsyncSession) -> dict[str, Any]:
    """Fetch the vault shard for one masked patient identifier."""

    try:
        result = await db.execute(
            select(NexaVault).where(NexaVault.masked_internal_id == patient_id).limit(1)
        )
    except SQLAlchemyError as exc:
        log_safe_exception(
            logger, exc, subsystem="database", operation="patient_vault_fetch"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Patient identity shard is temporarily unavailable.",
        ) from exc

    row = result.scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient record not found."
        )

    # Sprint 2: Transparent decryption with auto-migration
    return {
        "patient_name": await decrypt_vault_field(
            patient_id, "patient_name", row.patient_name, db
        ),
        "phone": await decrypt_vault_field(patient_id, "phone", row.phone, db),
        "aadhaar_abha_id": await decrypt_vault_field(
            patient_id, "aadhaar_abha_id", row.aadhaar_abha_id, db
        ),
    }


async def _fetch_clinical_shard(patient_id: str, db: AsyncSession) -> dict[str, Any]:
    """Fetch the clinical shard for one masked patient identifier."""

    try:
        result = await db.execute(
            select(NexaClinical)
            .where(NexaClinical.masked_internal_id == patient_id)
            .limit(1)
        )
    except SQLAlchemyError as exc:
        log_safe_exception(
            logger, exc, subsystem="database", operation="patient_clinical_fetch"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Patient clinical shard is temporarily unavailable.",
        ) from exc

    row = result.scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient record not found."
        )

    return _clinical_payload(row)


def _consent_error(exc: consent_engine.ConsentEngineUnavailable) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Consent service is temporarily unavailable.",
    )


@router.get("/{patient_id}/record", response_model=dict[str, JsonValue])
async def reconstruct_patient_record(
    patient_id: UUID,
    consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    purpose: str | None = Header(default=None, alias="X-Consent-Purpose"),
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.RECORD_READ)
    ),
    db: AsyncSession = Depends(get_db_session),
    kms: EncryptionProvider = Depends(get_kms_provider),
) -> dict[str, JsonValue]:
    """Reconstruct a patient record only through a scoped Redis capability.

    Atomic consent-gated decryption: Step 1 of Sprint 2 integration.
    """

    patient_id_text = str(patient_id)
    clinician_id = provider.actor_uid
    normalized_purpose = purpose.strip() if purpose else ""

    if not consent_token or not normalized_purpose:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active consent token and access purpose are required.",
        )

    # Atomically validate, audit, decrypt, and consume.
    response = await consent_gated_decrypt(
        patient_id=patient_id_text,
        consent_token=consent_token,
        purpose=normalized_purpose,
        requested_scope="*",  # Fetch all authorized fields in one atomic pass
        provider_id=clinician_id,
        hospital_id=str(provider.hospital_id),
        db=db,
        redis=get_consent_redis_client(),
        kms=kms,
        session_binding=provider.session_binding,
    )

    return response


class EmergencySummaryResponse(BaseModel):
    patient_id: str
    categories: dict[str, JsonValue]
    retrieved_at: str


@router.get("/{patient_id}/emergency-summary", response_model=EmergencySummaryResponse)
async def get_emergency_summary(
    patient_id: UUID,
    consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.EMERGENCY_ATTEMPT)
    ),
    db: AsyncSession = Depends(get_db_session),
) -> EmergencySummaryResponse:
    """Return only the clinical categories a live break-glass capability
    actually holds, for the authenticated provider/hospital/session.

    This is the *only* endpoint break-glass capabilities may be used
    against. It never accepts routine capabilities, never returns an
    unapproved category, and never echoes the bearer token back.
    """

    if not consent_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "BREAK_GLASS_TOKEN_REQUIRED"},
        )

    try:
        capability = await consent_engine.validate(
            token=consent_token,
            patient_id=str(patient_id),
            clinician_id=provider.actor_uid,
            purpose="EMERGENCY",
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
        )
    except consent_engine.ConsentEngineUnavailable as exc:
        raise _consent_error(exc) from exc

    if capability is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "BREAK_GLASS_CAPABILITY_INVALID_OR_EXPIRED"},
        )

    if not capability.is_break_glass:
        # Defect 1/2 contract: a routine capability must never satisfy this
        # endpoint, even if its purpose happened to be EMERGENCY.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "BREAK_GLASS_CAPABILITY_REQUIRED"},
        )

    try:
        categories = parse_clinical_categories(capability.scope)
    except UnsupportedClinicalCategoryError as exc:
        # A capability minted with a category outside the current canonical
        # vocabulary (e.g. issued under a retired protocol version) fails
        # closed rather than silently serving a subset.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": exc.error_code, "category": exc.category},
        ) from exc

    if not categories:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "BREAK_GLASS_CAPABILITY_REQUIRED"},
        )

    audit_success = await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=provider.actor_uid,
        event_type="BREAK_GLASS_EMERGENCY_SUMMARY_ACCESSED",
        target_id=str(patient_id),
        status="SUCCESS",
        metadata={
            "hospital_id": str(provider.hospital_id),
            "patient_id": str(patient_id),
            "reason_code": capability.reason_code,
            "reason_code_version": capability.reason_code_version,
            "category_protocol_version": capability.category_protocol_version,
            "categories": [c.value for c in categories],
        },
    )
    if not audit_success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audit ledger write failed; emergency summary access aborted.",
        )

    summary = await build_emergency_summary(patient_id, categories, db)
    return EmergencySummaryResponse(
        patient_id=summary.patient_id,
        categories=summary.categories,
        retrieved_at=summary.retrieved_at.isoformat(),
    )


@router.post("/{patient_id}/erase", response_model=ErasureResponse)
async def erase_patient_data(
    patient_id: UUID,
    payload: ErasureRequest,
    provider: ProviderContext = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db_session),
    kms: EncryptionProvider = Depends(get_kms_provider),
) -> ErasureResponse:
    """Trigger cryptographic erasure for a patient (Right to be Forgotten).

    Security Controls:
    - Gated by 'admin' role.
    - Explicit 'ERASE-<uuid>' confirmation required.
    - Hard-audit before and after destruction.
    """
    patient_id_str = str(patient_id)
    expected_conf = f"ERASE-{patient_id_str}"

    if payload.confirmation != expected_conf:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Confirmation string mismatch. Expected: {expected_conf}",
        )

    from app.observability.audit_ledger import append_audit_log_or_503

    # 1. Audit Request
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=provider.actor_uid,
        event_type="CRYPTOGRAPHIC_ERASURE_REQUESTED",
        target_id=patient_id_str,
        status="STARTED",
        metadata={"reason": payload.reason},
    )

    # 2. Execute Cryptographic Erasure
    destroy_succeeded = await kms.destroy_dek(patient_id_str, db)

    # 3. Read back the tombstone's real state -- never assume success.
    from sqlalchemy import select as _select

    from app.models.erasure_tombstone import PatientErasureTombstone

    tombstone = (
        await db.execute(
            _select(PatientErasureTombstone).where(
                PatientErasureTombstone.patient_ref == patient_id_str
            )
        )
    ).scalar_one_or_none()

    # 4. Audit Completion
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=provider.actor_uid,
        event_type="CRYPTOGRAPHIC_ERASURE_COMPLETED",
        target_id=patient_id_str,
        status="SUCCESS" if destroy_succeeded else "OPERATOR_ACTION_REQUIRED",
    )

    if tombstone is None:
        # Should be unreachable -- destroy_dek always creates one -- but
        # fail with a truthful "unknown" state rather than claiming erased.
        return ErasureResponse(
            status="unknown",
            patient_id=patient_id_str,
            assurance_level="unknown",
            wrapping_key_type="unknown",
            operator_action_required=True,
            historical_backup_irrecoverability_proven=False,
        )

    return ErasureResponse(
        status=tombstone.status,
        patient_id=patient_id_str,
        assurance_level=tombstone.assurance_level,
        wrapping_key_type=tombstone.wrapping_key_type,
        operator_action_required=tombstone.operator_action_required,
        # Only a patient-specific key that has actually reached the
        # "destroyed" assurance level ever supports this claim. A
        # shared-key patient (access-blocked only) or an AWS key still in
        # its mandatory pending-deletion window never does.
        historical_backup_irrecoverability_proven=(
            tombstone.wrapping_key_type == "patient"
            and tombstone.assurance_level == "patient_key_destroyed"
        ),
    )
