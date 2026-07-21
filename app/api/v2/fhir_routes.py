"""FHIR R4 export routes for Nexa Care V2."""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_active_consent
from app.models.patient_records import Allergy, LabResult, Medication, TimelineEvent, Vitals
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


async def _scalars_all(db: AsyncSession, stmt) -> list[object]:
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _common_provenance(row: object) -> dict:
    return {
        "source": getattr(row, "source", None),
        "confidence": getattr(row, "confidence", None),
        "risk_level": getattr(row, "risk_level", None),
        "source_document_id": str(getattr(row, "source_document_id", None)) if getattr(row, "source_document_id", None) else None,
    }


async def _fetch_structured_records(patient_id: str, db: AsyncSession) -> list[dict]:
    """Read current structured clinical rows for FHIR export."""

    pid = UUID(patient_id)
    vitals = await _scalars_all(db, select(Vitals).where(Vitals.patient_id == pid).order_by(Vitals.recorded_at.desc()))
    medications = await _scalars_all(db, select(Medication).where(Medication.patient_id == pid).order_by(Medication.prescribed_at.desc()))
    labs = await _scalars_all(db, select(LabResult).where(LabResult.patient_id == pid).order_by(LabResult.recorded_at.desc()))
    allergies = await _scalars_all(db, select(Allergy).where(Allergy.patient_id == pid).order_by(Allergy.severity.desc()))
    timeline = await _scalars_all(db, select(TimelineEvent).where(TimelineEvent.patient_id == pid).order_by(TimelineEvent.occurred_at.desc()).limit(50))

    records: list[dict] = []
    records.extend(
        {
            "record_type": "vital",
            "type": row.type,
            "value": row.value,
            "unit": row.unit,
            "recorded_at": row.recorded_at.isoformat(),
            **_common_provenance(row),
        }
        for row in vitals
    )
    records.extend(
        {
            "record_type": "medication",
            "name": row.name,
            "strength": row.strength,
            "frequency": row.frequency,
            "prescribed_at": row.prescribed_at.isoformat(),
            **_common_provenance(row),
        }
        for row in medications
    )
    records.extend(
        {
            "record_type": "lab",
            "test_name": row.test_name,
            "value": row.value,
            "unit": row.unit,
            "reference_range": row.reference_range,
            "is_abnormal": row.is_abnormal,
            "recorded_at": row.recorded_at.isoformat(),
            **_common_provenance(row),
        }
        for row in labs
    )
    records.extend(
        {
            "record_type": "allergy",
            "allergen": row.allergen,
            "severity": row.severity,
            **_common_provenance(row),
        }
        for row in allergies
    )
    records.extend(
        {
            "record_type": "timeline_diagnosis",
            "summary": row.summary,
            "occurred_at": row.occurred_at.isoformat(),
        }
        for row in timeline
        if any(term in row.summary.lower() for term in ("diagnosis", "diabetes", "hypertension", "condition"))
    )
    return records


async def _fetch_legacy_clinical_records(patient_id: str, db: AsyncSession) -> list[dict]:
    """Read deprecated clinical shard rows as a backward-compatible fallback."""

    result = await db.execute(
        text(
            "SELECT masked_internal_id, diagnoses, lab_results, prescriptions "
            "FROM nexa_clinical "
            "WHERE masked_internal_id = :patient_id"
        ),
        {"patient_id": patient_id},
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def _fetch_clinical_records(patient_id: str, db: AsyncSession) -> list[dict]:
    """Read current structured records first, with legacy clinical shard fallback."""

    structured_records = await _fetch_structured_records(patient_id, db)
    if structured_records:
        return structured_records
    return await _fetch_legacy_clinical_records(patient_id, db)


@router.get("/export/{patient_id}", response_model=FHIRBundleResponse)
async def export_fhir_bundle(
    patient_id: UUID,
    provider: ProviderContext = Depends(require_active_consent),
    db: AsyncSession = Depends(get_db_session),
) -> FHIRBundleResponse:
    """Export current clinical history as a FHIR R4 Bundle."""

    patient_id_text = str(patient_id)
    clinical_records = await _fetch_clinical_records(patient_id_text, db)
    bundle = generate_fhir_bundle(patient_id_text, clinical_records)
    exported_at = datetime.now(timezone.utc).isoformat()

    try:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
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
                "source": "structured_patient_records" if clinical_records and clinical_records[0].get("record_type") else "legacy_nexa_clinical_fallback",
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
