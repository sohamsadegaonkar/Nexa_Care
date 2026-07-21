# ruff: noqa: E402
"""Seed the canonical Nexa Care alpha patient and clinical demo records.

The authoritative ``patients`` row contains only the canonical identifier and
account status.  Demo PII is intentionally not copied into plaintext-capable
columns; the encrypted vault is not needed for patient authentication or
device enrollment.

All authoritative and clinical writes share one transaction.  Re-running the
seeder fills missing demo records without duplicating existing records.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_engine, get_session_factory
from app.models.patient import Patient
from app.models.patient_records import (
    Allergy,
    DocumentReference,
    LabResult,
    Medication,
    PatientRecord,
    TimelineEvent,
    Vitals,
)
from scripts.demo_environment import require_demo_environment

DEMO_PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
DEMO_PATIENT_UUID = uuid.UUID(DEMO_PATIENT_ID)
DEMO_DOC_UUID = uuid.UUID("444e4567-e89b-12d3-a456-426614174444")


class DemoPatientConflict(RuntimeError):
    """Existing canonical data is incompatible with the demo patient."""


def _require_values(record: object, **expected: object) -> None:
    if any(getattr(record, field) != value for field, value in expected.items()):
        raise DemoPatientConflict(f"existing {type(record).__name__} conflicts with canonical demo data")


def _validate_existing_patient(patient: Patient) -> None:
    if patient.is_deleted:
        raise DemoPatientConflict("canonical patient is soft-deleted")


async def _ensure_authoritative_patient(session: AsyncSession) -> str:
    patient = await session.get(Patient, DEMO_PATIENT_UUID)
    if patient is not None:
        _validate_existing_patient(patient)
        return "already-exists"

    session.add(
        Patient(
            patient_uuid=DEMO_PATIENT_UUID,
            is_deleted=False,
            dek_id=None,
        )
    )
    # Establish the authoritative row before any dependent clinical inserts.
    await session.flush()
    return "created"


async def _seed_clinical_records(session: AsyncSession) -> None:
    patient_id = DEMO_PATIENT_UUID
    now = datetime.now(timezone.utc)

    if await session.scalar(select(PatientRecord).where(PatientRecord.patient_id == patient_id)) is None:
        session.add(PatientRecord(patient_id=patient_id))

    document = await session.get(DocumentReference, DEMO_DOC_UUID)
    if document is None:
        session.add(
            DocumentReference(
                id=DEMO_DOC_UUID,
                patient_id=patient_id,
                document_type="LAB_REPORT",
                uploaded_at=now - timedelta(hours=3),
                storage_ref="s3://nexa-care-demo/aarav_sharma_hba1c_report.pdf",
            )
        )
    elif document.patient_id != patient_id:
        raise DemoPatientConflict("canonical document UUID belongs to another patient")
    else:
        _require_values(
            document,
            document_type="LAB_REPORT",
            storage_ref="s3://nexa-care-demo/aarav_sharma_hba1c_report.pdf",
        )

    vitals = (
        ("BP", "130/85", "mmHg", "LOW_RISK"),
        ("SUGAR", "145", "mg/dL", "MEDIUM_RISK"),
        ("HR", "78", "bpm", "LOW_RISK"),
    )
    for vital_type, value, unit, risk_level in vitals:
        existing = await session.scalar(
            select(Vitals).where(Vitals.patient_id == patient_id, Vitals.type == vital_type)
        )
        if existing is None:
            session.add(
                Vitals(
                    patient_id=patient_id,
                    type=vital_type,
                    value=value,
                    unit=unit,
                    recorded_at=now - timedelta(days=2),
                    source="manual",
                    risk_level=risk_level,
                )
            )
        else:
            _require_values(
                existing,
                value=value,
                unit=unit,
                source="manual",
                risk_level=risk_level,
            )

    medication = await session.scalar(
        select(Medication).where(Medication.patient_id == patient_id, Medication.name == "Metformin")
    )
    if medication is None:
        session.add(
            Medication(
                patient_id=patient_id,
                name="Metformin",
                strength="500mg",
                frequency="Twice daily",
                prescribed_at=now - timedelta(days=14),
                source="manual",
                risk_level="MEDIUM_RISK",
            )
        )
    else:
        _require_values(
            medication,
            strength="500mg",
            frequency="Twice daily",
            source="manual",
            risk_level="MEDIUM_RISK",
        )

    allergy = await session.scalar(
        select(Allergy).where(Allergy.patient_id == patient_id, Allergy.allergen == "Penicillin")
    )
    if allergy is None:
        session.add(
            Allergy(
                patient_id=patient_id,
                allergen="Penicillin",
                severity="Severe",
                source="manual",
                risk_level="HIGH_RISK",
            )
        )
    else:
        _require_values(
            allergy,
            severity="Severe",
            source="manual",
            risk_level="HIGH_RISK",
        )

    lab_result = await session.scalar(
        select(LabResult).where(LabResult.patient_id == patient_id, LabResult.test_name == "HbA1c")
    )
    if lab_result is None:
        session.add(
            LabResult(
                patient_id=patient_id,
                test_name="HbA1c",
                value="7.2",
                unit="%",
                reference_range="4.0-5.6 %",
                is_abnormal=True,
                recorded_at=now - timedelta(hours=2),
                source="ai_extracted",
                confidence=0.96,
                risk_level="HIGH_RISK",
                source_document_id=DEMO_DOC_UUID,
            )
        )
    else:
        _require_values(
            lab_result,
            value="7.2",
            unit="%",
            reference_range="4.0-5.6 %",
            is_abnormal=True,
            source="ai_extracted",
            confidence=0.96,
            risk_level="HIGH_RISK",
            source_document_id=DEMO_DOC_UUID,
        )

    existing_timeline = (
        await session.scalars(select(TimelineEvent).where(TimelineEvent.patient_id == patient_id))
    ).all()
    existing_events = {(event.event_type, event.summary) for event in existing_timeline}
    timeline = (
        (
            "ENCOUNTER",
            now - timedelta(days=14),
            "manual",
            "Encounter recorded: Initial Type 2 Diabetes management checkup",
            None,
        ),
        (
            "MEDICATION",
            now - timedelta(days=14),
            "manual",
            "Medication prescribed: Metformin 500mg (Twice daily)",
            None,
        ),
        (
            "ALLERGY",
            now - timedelta(days=14),
            "manual",
            "Allergy recorded: Penicillin (Severe)",
            None,
        ),
        (
            "VITALS",
            now - timedelta(days=2),
            "manual",
            "Vitals recorded: BP 130/85 mmHg, Blood Sugar 145 mg/dL, HR 78 bpm",
            None,
        ),
        (
            "DOCUMENT",
            now - timedelta(hours=3),
            "manual",
            "Document uploaded: Diagnostic Lab Report (HbA1c Panel)",
            None,
        ),
        (
            "EXTRACTED_DATA_INGESTED",
            now - timedelta(hours=2),
            "ai_extracted",
            "AI ingested HbA1c: 7.2 % (Confidence: 0.96)",
            DEMO_DOC_UUID,
        ),
    )
    for event_type, occurred_at, source, summary, event_ref_id in timeline:
        if (event_type, summary) not in existing_events:
            session.add(
                TimelineEvent(
                    patient_id=patient_id,
                    event_type=event_type,
                    event_ref_id=event_ref_id,
                    occurred_at=occurred_at,
                    source=source,
                    summary=summary,
                )
            )


async def seed_aarav_sharma(session: AsyncSession) -> str:
    """Stage the complete canonical seed in ``session`` without committing it."""

    status = await _ensure_authoritative_patient(session)
    await _seed_clinical_records(session)
    return status


async def _run() -> int:
    require_demo_environment("seed_demo_patient")

    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            try:
                status = await seed_aarav_sharma(session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except DemoPatientConflict:
        print(f"status=rejected patient_id={DEMO_PATIENT_ID} reason=conflict", file=sys.stderr)
        return 1
    finally:
        await get_async_engine().dispose()

    print(f"status={status} patient_id={DEMO_PATIENT_ID}")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
