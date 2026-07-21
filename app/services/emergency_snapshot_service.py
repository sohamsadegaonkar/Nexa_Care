"""Emergency snapshot retrieval service.

Emergency reads use the current structured patient-record tables as the source
of truth. The legacy nexa_emergency_snapshot projection remains a
backward-compatible fallback only; it is not allowed to hide real structured
medical data by returning an empty projection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import JsonValue
from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_records import (
    Allergy,
    LabResult,
    Medication,
    TimelineEvent,
    Vitals,
)
from app.observability.safe_exceptions import log_safe_exception

logger = logging.getLogger("nexa_logger")

_NO_KNOWN_MEDICAL_DATA_MESSAGE = "No Known Medical Data"


def _to_json_value(value: object) -> JsonValue:
    """Convert common DB scalar values into JSON-safe response values."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    return str(value)


def _serialize_snapshot_row(row: dict[str, object]) -> dict[str, JsonValue]:
    """Serialize a SQLAlchemy mapping without making clinical assumptions."""

    return {key: _to_json_value(value) for key, value in row.items()}


def _provenance(row: object) -> dict[str, JsonValue]:
    """Return common provenance fields from a structured clinical row."""

    return {
        "source": _to_json_value(getattr(row, "source", None)),
        "confidence": _to_json_value(getattr(row, "confidence", None)),
        "risk_level": _to_json_value(getattr(row, "risk_level", None)),
        "source_document_id": _to_json_value(getattr(row, "source_document_id", None)),
    }


def _latest_timestamp(rows: list[object]) -> datetime | None:
    """Return the newest timestamp-like attribute from emergency-relevant rows."""

    latest: datetime | None = None
    for row in rows:
        for attr in ("recorded_at", "prescribed_at", "occurred_at"):
            value = getattr(row, attr, None)
            if isinstance(value, datetime) and (latest is None or value > latest):
                latest = value
    return latest


async def _scalars_all(db_session: AsyncSession, stmt) -> list[object]:
    result = await db_session.execute(stmt)
    return list(result.scalars().all())


async def _fetch_structured_snapshot(
    patient_id: UUID, db_session: AsyncSession
) -> dict[str, JsonValue]:
    """Build an emergency snapshot from current structured clinical records."""

    allergies = await _scalars_all(
        db_session,
        select(Allergy)
        .where(Allergy.patient_id == patient_id)
        .order_by(Allergy.severity.desc()),
    )
    medications = await _scalars_all(
        db_session,
        select(Medication)
        .where(Medication.patient_id == patient_id)
        .order_by(Medication.prescribed_at.desc()),
    )
    vitals = await _scalars_all(
        db_session,
        select(Vitals)
        .where(Vitals.patient_id == patient_id)
        .order_by(Vitals.recorded_at.desc()),
    )
    labs = await _scalars_all(
        db_session,
        select(LabResult)
        .where(LabResult.patient_id == patient_id)
        .order_by(LabResult.recorded_at.desc()),
    )
    timeline_events = await _scalars_all(
        db_session,
        select(TimelineEvent)
        .where(TimelineEvent.patient_id == patient_id)
        .order_by(TimelineEvent.occurred_at.desc())
        .limit(20),
    )

    if not any((allergies, medications, vitals, labs, timeline_events)):
        return {}

    serialized_allergies = [
        {
            "allergen": allergy.allergen,
            "severity": allergy.severity,
            **_provenance(allergy),
        }
        for allergy in allergies
    ]
    serialized_meds = [
        {
            "name": med.name,
            "strength": med.strength,
            "frequency": med.frequency,
            "prescribed_at": _to_json_value(med.prescribed_at),
            **_provenance(med),
        }
        for med in medications
    ]
    serialized_vitals = [
        {
            "type": vital.type,
            "value": vital.value,
            "unit": vital.unit,
            "recorded_at": _to_json_value(vital.recorded_at),
            **_provenance(vital),
        }
        for vital in vitals
    ]
    serialized_labs = [
        {
            "test_name": lab.test_name,
            "value": lab.value,
            "unit": lab.unit,
            "reference_range": lab.reference_range,
            "is_abnormal": lab.is_abnormal,
            "recorded_at": _to_json_value(lab.recorded_at),
            **_provenance(lab),
        }
        for lab in labs
    ]
    high_risk_allergies = [
        allergy
        for allergy in serialized_allergies
        if str(allergy.get("risk_level") or "").upper()
        in {"HIGH_RISK", "CRITICAL_RISK"}
        or str(allergy.get("severity") or "").lower()
        in {"severe", "anaphylaxis", "critical"}
    ]
    abnormal_labs = [
        lab
        for lab in serialized_labs
        if lab["is_abnormal"]
        or str(lab.get("risk_level") or "").upper() in {"HIGH_RISK", "CRITICAL_RISK"}
    ]
    critical_diagnoses = [
        event.summary
        for event in timeline_events
        if any(
            term in event.summary.lower()
            for term in ("diabetes", "critical", "diagnosis")
        )
    ]
    last_updated = _latest_timestamp(
        [*allergies, *medications, *vitals, *labs, *timeline_events]
    )

    return {
        "source": "structured_patient_records",
        "allergies": serialized_allergies,
        "high_risk_allergies": high_risk_allergies,
        "active_medications": serialized_meds,
        "latest_vitals": serialized_vitals[:5],
        "lab_results": serialized_labs,
        "abnormal_labs": abnormal_labs,
        "critical_diagnoses": critical_diagnoses,
        "last_updated": _to_json_value(last_updated),
    }


async def _fetch_legacy_projection(
    patient_id: UUID, db_session: AsyncSession
) -> dict[str, JsonValue] | None:
    """Return the deprecated emergency projection row, if one exists."""

    stmt = text(
        "SELECT * FROM nexa_emergency_snapshot "
        "WHERE patient_id = :patient_id LIMIT 1"
    ).bindparams(bindparam("patient_id", type_=PG_UUID(as_uuid=True)))
    result = await db_session.execute(stmt, {"patient_id": patient_id})
    row = result.mappings().first()
    if row is None:
        return None
    snapshot = _serialize_snapshot_row(dict(row))
    snapshot.setdefault("source", "legacy_emergency_projection")
    return snapshot


async def get_emergency_snapshot(
    patient_id: UUID, db_session: AsyncSession
) -> dict[str, object]:
    """Return emergency-visible medical facts for a masked patient UUID."""

    try:
        structured_snapshot = await _fetch_structured_snapshot(patient_id, db_session)
    except SQLAlchemyError as exc:
        log_safe_exception(
            logger,
            logging.ERROR,
            "emergency_snapshot_retrieval_failed",
            exc,
            subsystem="database",
            operation="emergency_snapshot",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Emergency snapshot retrieval is temporarily unavailable.",
        ) from exc

    retrieved_at = datetime.now(timezone.utc)

    if structured_snapshot:
        return {
            "patient_id": patient_id,
            "snapshot_status": "available",
            "message": None,
            "snapshot": structured_snapshot,
            "retrieved_at": retrieved_at,
        }

    try:
        legacy_snapshot = await _fetch_legacy_projection(patient_id, db_session)
    except SQLAlchemyError as exc:
        log_safe_exception(
            logger, exc, subsystem="database", operation="legacy_emergency_projection"
        )
        legacy_snapshot = None

    if legacy_snapshot is None:
        return {
            "patient_id": patient_id,
            "snapshot_status": "no_known_medical_data",
            "message": _NO_KNOWN_MEDICAL_DATA_MESSAGE,
            "snapshot": {},
            "retrieved_at": retrieved_at,
        }

    return {
        "patient_id": patient_id,
        "snapshot_status": "available",
        "message": None,
        "snapshot": legacy_snapshot,
        "retrieved_at": retrieved_at,
    }
