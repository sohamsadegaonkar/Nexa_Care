"""Category-filtered emergency summary for the dedicated break-glass route.

This is deliberately separate from ``emergency_snapshot_service.py`` (used by
the NFC ``/read-card`` flow, which returns an unfiltered snapshot once a card
is resolved) and from ``consent_gated_crypto.py`` (the routine field-scope
decrypt path). Break-glass access is scoped to whole *clinical categories*
(``app.security.clinical_categories.ClinicalCategory``) that were validated
and narrowed at grant-issue time -- this module returns exactly those
categories, with source and verification state for every clinically
sensitive value, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_records import Allergy, DocumentReference, LabResult, Medication, TimelineEvent, Vitals
from app.security.clinical_categories import ClinicalCategory


def _to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _provenance(row: object) -> dict[str, JsonValue]:
    """Common source/verification fields for a structured clinical row.

    Every clinically sensitive value returned by the emergency summary
    carries this, so a consuming clinician can see how confident the data
    is, not just the value itself.
    """

    return {
        "source": _to_json_value(getattr(row, "source", None)),
        "confidence": _to_json_value(getattr(row, "confidence", None)),
        "risk_level": _to_json_value(getattr(row, "risk_level", None)),
        "source_document_id": _to_json_value(getattr(row, "source_document_id", None)),
        "verified": str(getattr(row, "source", "")) != "ai_extracted",
    }


async def _scalars_all(db_session: AsyncSession, stmt) -> list[object]:
    result = await db_session.execute(stmt)
    return list(result.scalars().all())


async def _build_allergies(patient_id: UUID, db: AsyncSession) -> dict[str, JsonValue]:
    rows = await _scalars_all(
        db, select(Allergy).where(Allergy.patient_id == patient_id).order_by(Allergy.severity.desc())
    )
    return {
        "category": ClinicalCategory.ALLERGIES.value,
        "available": bool(rows),
        "items": [
            {"allergen": r.allergen, "severity": r.severity, **_provenance(r)}
            for r in rows
        ],
    }


async def _build_active_medications(patient_id: UUID, db: AsyncSession) -> dict[str, JsonValue]:
    rows = await _scalars_all(
        db,
        select(Medication).where(Medication.patient_id == patient_id).order_by(Medication.prescribed_at.desc()),
    )
    return {
        "category": ClinicalCategory.ACTIVE_MEDICATIONS.value,
        "available": bool(rows),
        "items": [
            {
                "name": r.name,
                "strength": r.strength,
                "frequency": r.frequency,
                "prescribed_at": _to_json_value(r.prescribed_at),
                **_provenance(r),
            }
            for r in rows
        ],
    }


async def _build_vitals(patient_id: UUID, db: AsyncSession) -> dict[str, JsonValue]:
    rows = await _scalars_all(
        db,
        select(Vitals).where(Vitals.patient_id == patient_id).order_by(Vitals.recorded_at.desc()).limit(5),
    )
    return {
        "category": ClinicalCategory.VITALS.value,
        "available": bool(rows),
        "items": [
            {
                "type": r.type,
                "value": r.value,
                "unit": r.unit,
                "recorded_at": _to_json_value(r.recorded_at),
                **_provenance(r),
            }
            for r in rows
        ],
    }


async def _build_lab_results(patient_id: UUID, db: AsyncSession) -> dict[str, JsonValue]:
    rows = await _scalars_all(
        db,
        select(LabResult).where(LabResult.patient_id == patient_id).order_by(LabResult.recorded_at.desc()),
    )
    return {
        "category": ClinicalCategory.LAB_RESULTS.value,
        "available": bool(rows),
        "items": [
            {
                "test_name": r.test_name,
                "value": r.value,
                "unit": r.unit,
                "reference_range": r.reference_range,
                "is_abnormal": r.is_abnormal,
                "recorded_at": _to_json_value(r.recorded_at),
                **_provenance(r),
            }
            for r in rows
        ],
    }


async def _build_diagnoses(patient_id: UUID, db: AsyncSession) -> dict[str, JsonValue]:
    """Diagnoses are not (yet) a dedicated structured record in this
    repository -- there is no ``Diagnosis`` model. The only existing
    authoritative-ish signal is the patient's ``TimelineEvent`` log, which
    is written for real record-append events. We surface those events
    honestly, labelled with their real source, rather than fabricating a
    stronger guarantee than the data actually has.
    """

    rows = await _scalars_all(
        db,
        select(TimelineEvent)
        .where(TimelineEvent.patient_id == patient_id)
        .order_by(TimelineEvent.occurred_at.desc())
        .limit(20),
    )
    return {
        "category": ClinicalCategory.DIAGNOSES.value,
        "available": bool(rows),
        "items": [
            {
                "summary": r.summary,
                "event_type": r.event_type,
                "occurred_at": _to_json_value(r.occurred_at),
                "source": r.source,
                "verified": r.source != "ai_extracted",
            }
            for r in rows
        ],
        "caveat": "Derived from the patient timeline log, not a dedicated diagnosis record.",
    }


async def _build_blood_group(patient_id: UUID, db: AsyncSession) -> dict[str, JsonValue]:
    """There is no verified blood-group source in this repository yet.

    Per Defect 2, an unverified or default blood-group value must never be
    returned. This category always reports ``available: false`` with an
    explicit ``verified: false`` state until a real, verified source exists.
    """

    return {
        "category": ClinicalCategory.BLOOD_GROUP.value,
        "available": False,
        "value": None,
        "verified": False,
        "verification_state": "no_verified_source",
    }


async def _build_document_references(patient_id: UUID, db: AsyncSession) -> dict[str, JsonValue]:
    rows = await _scalars_all(
        db,
        select(DocumentReference)
        .where(DocumentReference.patient_id == patient_id)
        .order_by(DocumentReference.uploaded_at.desc())
        .limit(20),
    )
    return {
        "category": ClinicalCategory.DOCUMENT_REFERENCES.value,
        "available": bool(rows),
        # Deliberately omits `storage_ref` (a direct storage locator) --
        # emergency responders get document existence/type/timestamp, not
        # a raw pointer to encrypted storage. Full document retrieval goes
        # through the existing authenticated document endpoints.
        "items": [
            {
                "document_id": str(r.id),
                "document_type": r.document_type,
                "uploaded_at": _to_json_value(r.uploaded_at),
            }
            for r in rows
        ],
    }


_BUILDERS = {
    ClinicalCategory.ALLERGIES: _build_allergies,
    ClinicalCategory.ACTIVE_MEDICATIONS: _build_active_medications,
    ClinicalCategory.VITALS: _build_vitals,
    ClinicalCategory.LAB_RESULTS: _build_lab_results,
    ClinicalCategory.DIAGNOSES: _build_diagnoses,
    ClinicalCategory.BLOOD_GROUP: _build_blood_group,
    ClinicalCategory.DOCUMENT_REFERENCES: _build_document_references,
}


@dataclass(frozen=True, slots=True)
class EmergencySummary:
    patient_id: str
    categories: dict[str, JsonValue]
    retrieved_at: datetime


async def build_emergency_summary(
    patient_id: UUID,
    categories: list[ClinicalCategory],
    db: AsyncSession,
) -> EmergencySummary:
    """Build a summary containing only the given, already-validated categories.

    Callers are responsible for validating that ``categories`` came from a
    live, non-expired, non-revoked break-glass capability -- this function
    trusts its input and does not re-check consent.
    """

    result: dict[str, JsonValue] = {}
    for category in categories:
        builder = _BUILDERS.get(category)
        if builder is None:
            # Defensive: should be unreachable because ClinicalCategory is
            # exhaustively mapped above, but fail closed rather than
            # silently omitting a category that should have had data.
            raise ValueError(f"No emergency-summary builder registered for {category!r}")
        result[category.value] = await builder(patient_id, db)

    return EmergencySummary(
        patient_id=str(patient_id),
        categories=result,
        retrieved_at=datetime.now(timezone.utc),
    )