"""FHIR R4 export routes for Nexa Care V2."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_active_consent
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.services.fhir_converter import generate_fhir_bundle

router = APIRouter(prefix="/api/v2/fhir", tags=["fhir"])


class FHIRBundleResponse(BaseModel):
    """FHIR Bundle response wrapper with strict top-level validation."""

    model_config = ConfigDict(frozen=True, extra="allow")

    resourceType: str
    id: str
    type: str
    entry: list[dict[str, JsonValue]]


async def _fetch_clinical_records(patient_id: str, db: AsyncSession) -> list[dict]:
    """Read clinical shard rows for a masked patient id only."""

    result = await db.execute(
        text(
            "SELECT masked_internal_id, diagnoses, lab_results, prescriptions "
            "FROM nexa_clinical "
            "WHERE masked_internal_id = :patient_id"
        ),
        {"patient_id": patient_id},
    )
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/export/{patient_id}", response_model=FHIRBundleResponse)
async def export_fhir_bundle(
    patient_id: UUID,
    provider: ProviderContext = Depends(require_active_consent),
    db: AsyncSession = Depends(get_db_session),
) -> FHIRBundleResponse:
    """Export clinical history as a FHIR R4 Bundle.

    Security cascade:
    1. Provider authentication is enforced inside ``require_active_consent``.
    2. ``X-Consent-Token`` must be live in Redis and bound to this provider and
       patient path parameter.
    3. Only clinical shard records are read; vault identity data is never joined.
    4. Export is audited without storing clinical payload contents in the ledger.
    """

    patient_id_text = str(patient_id)
    clinical_records = await _fetch_clinical_records(patient_id_text, db)
    bundle = generate_fhir_bundle(patient_id_text, clinical_records)
    exported_at = datetime.now(timezone.utc).isoformat()

    try:
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="FHIR_BUNDLE_EXPORTED",
            target_id=patient_id_text,
            status="SUCCESS",
            metadata={
                "patient_id": patient_id_text,
                "provider_uid": provider.actor_uid,
                "hospital_id": str(provider.hospital.hospital_id),
                "exported_at": exported_at,
                "resource_count": len(bundle.get("entry", [])),
            },
            event_timestamp=exported_at,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audit ledger write failed; FHIR export aborted.",
        ) from exc

    return FHIRBundleResponse.model_validate(bundle)
