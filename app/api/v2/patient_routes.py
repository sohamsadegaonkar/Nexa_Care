"""Consent-scoped patient reconstruction routes for Nexa Care V2."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider, require_role
from app.models.provider_context import ProviderContext
from app.models.shards import NexaClinical, NexaVault
import app.services.consent_engine as consent_engine
from app.services.sharding import decrypt_vault_field
from app.services.consent_gated_crypto import consent_gated_decrypt, EncryptionProvider
from app.services.consent_engine import get_consent_redis_client
from app.services.crypto_kms import get_encryption_provider

logger = logging.getLogger("nexa_logger")
from app.observability.safe_exceptions import log_safe_exception

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
    vault_data_recoverable: bool


def _merge_non_null_fields(base: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
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
        log_safe_exception(logger, exc, subsystem="database", operation="patient_vault_fetch")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Patient identity shard is temporarily unavailable.",
        ) from exc

    row = result.scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient record not found.")

    # Sprint 2: Transparent decryption with auto-migration
    return {
        "patient_name": await decrypt_vault_field(patient_id, "patient_name", row.patient_name, db),
        "phone": await decrypt_vault_field(patient_id, "phone", row.phone, db),
        "aadhaar_abha_id": await decrypt_vault_field(patient_id, "aadhaar_abha_id", row.aadhaar_abha_id, db),
    }


async def _fetch_clinical_shard(patient_id: str, db: AsyncSession) -> dict[str, Any]:
    """Fetch the clinical shard for one masked patient identifier."""

    try:
        result = await db.execute(
            select(NexaClinical).where(NexaClinical.masked_internal_id == patient_id).limit(1)
        )
    except SQLAlchemyError as exc:
        log_safe_exception(logger, exc, subsystem="database", operation="patient_clinical_fetch")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Patient clinical shard is temporarily unavailable.",
        ) from exc

    row = result.scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient record not found.")

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
    provider: ProviderContext = Depends(get_current_provider),
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
        actor_uid=provider.actor_uid,
        event_type="CRYPTOGRAPHIC_ERASURE_REQUESTED",
        target_id=patient_id_str,
        status="STARTED",
        metadata={"reason": payload.reason}
    )

    # 2. Execute Cryptographic Erasure
    # This overwrites DEKs and deletes them, invalidating cache.
    await kms.destroy_dek(patient_id_str, db)

    # 3. Audit Completion
    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="CRYPTOGRAPHIC_ERASURE_COMPLETED",
        target_id=patient_id_str,
        status="SUCCESS"
    )

    return ErasureResponse(
        status="erased",
        patient_id=patient_id_str,
        vault_data_recoverable=False
    )
