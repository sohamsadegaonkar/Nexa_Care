"""Consent-scoped patient reconstruction routes for Nexa Care V2."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.models.provider_context import ProviderContext
from app.models.secure_record import SecureMergedRecord
from app.models.shards import NexaClinical, NexaVault
import app.services.consent_engine as consent_engine
from app.services.audit import audit_read

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/patient", tags=["patient"])


def _merge_non_null_fields(base: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Return a copy containing only non-null shard fields."""

    merged = dict(base)
    for key, value in values.items():
        if value is not None:
            merged[key] = value
    return merged


def _pii_payload(row: NexaVault) -> dict[str, Any]:
    """Build an identity-only payload without clinical fields."""

    raw_pii = row.raw_pii if isinstance(row.raw_pii, dict) else {}
    return _merge_non_null_fields(
        raw_pii,
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
        logger.critical(json.dumps({
            "event": "patient_record_reconstruction_db_error",
            "shard": "nexa_vault",
            "patient_id": patient_id,
            "exception": str(exc),
            "action": "raising_503_fail_closed",
        }))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Patient identity shard is temporarily unavailable.",
        ) from exc

    row = result.scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient record not found.")

    return _pii_payload(row)


async def _fetch_clinical_shard(patient_id: str, db: AsyncSession) -> dict[str, Any]:
    """Fetch the clinical shard for one masked patient identifier."""

    try:
        result = await db.execute(
            select(NexaClinical).where(NexaClinical.masked_internal_id == patient_id).limit(1)
        )
    except SQLAlchemyError as exc:
        logger.critical(json.dumps({
            "event": "patient_record_reconstruction_db_error",
            "shard": "nexa_clinical",
            "patient_id": patient_id,
            "exception": str(exc),
            "action": "raising_503_fail_closed",
        }))
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
) -> dict[str, JsonValue]:
    """Reconstruct a patient record only through a scoped Redis capability."""

    patient_id_text = str(patient_id)
    clinician_id = provider.actor_uid
    normalized_purpose = purpose.strip() if purpose else ""

    if not consent_token or not normalized_purpose:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active consent token and access purpose are required.",
        )

    try:
        capability = await consent_engine.validate(
            token=consent_token,
            patient_id=patient_id_text,
            clinician_id=clinician_id,
            purpose=normalized_purpose,
        )
    except consent_engine.ConsentEngineUnavailable as exc:
        raise _consent_error(exc) from exc

    if capability is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active consent token required or expired.",
        )

    async with audit_read(clinician_id, patient_id_text, normalized_purpose):
        try:
            revalidated_capability = await consent_engine.validate(
                token=consent_token,
                patient_id=patient_id_text,
                clinician_id=clinician_id,
                purpose=normalized_purpose,
            )
        except consent_engine.ConsentEngineUnavailable as exc:
            raise _consent_error(exc) from exc

        if revalidated_capability is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active consent token required or expired.",
            )

        pii = await _fetch_pii_shard(patient_id_text, db)
        clinical = await _fetch_clinical_shard(patient_id_text, db)
        record = SecureMergedRecord(pii, clinical)
        response = record.to_response(revalidated_capability.scope)

        try:
            consumed_capability = await consent_engine.consume(
                db=db,
                token=consent_token,
                patient_id=patient_id_text,
                clinician_id=clinician_id,
                purpose=normalized_purpose,
            )
        except consent_engine.ConsentEngineUnavailable as exc:
            raise _consent_error(exc) from exc

        if consumed_capability is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Consent token was revoked before completion.",
            )

        return response