"""Canonical Demo Patient Seeder for Nexa Care V2 Alpha Demo (Days 12-13).

Seeds rich clinical records for canonical demo patient Aarav Sharma
(123e4567-e89b-12d3-a456-426614174001):
  - Patient Account & Demographics: Age 42, Type 2 Diabetes
  - Vitals: BP 130/85, Blood Sugar 145 mg/dL, Heart Rate 78 bpm (source="manual")
  - Medication: Metformin 500mg twice daily (source="manual")
  - Allergy: Penicillin severe sensitivity (source="manual", HIGH_RISK)
  - Lab Result: HbA1c 7.2% abnormal flagged (source="ai_extracted", confidence=0.96)
  - Chronological Timeline & Document Reference

Strictly idempotent: safe to execute repeatedly without duplicating rows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Add parent directory to path so app modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_async_engine
from app.models.patient_records import (
    Allergy,
    DocumentReference,
    LabResult,
    Medication,
    PatientRecord,
    TimelineEvent,
    Vitals,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEMO_PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
DEMO_DOC_ID = "444e4567-e89b-12d3-a456-426614174444"


async def seed_aarav_sharma(session: AsyncSession) -> None:
    pid_uuid = uuid.UUID(DEMO_PATIENT_ID)
    doc_uuid = uuid.UUID(DEMO_DOC_ID)
    now = datetime.now(timezone.utc)

    # 1. Ensure PatientRecord anchor exists
    stmt_pr = select(PatientRecord).where(PatientRecord.patient_id == pid_uuid)
    res_pr = await session.execute(stmt_pr)
    if res_pr.scalar_one_or_none() is None:
        logger.info(" -> Creating PatientRecord anchor for Aarav Sharma...")
        session.add(PatientRecord(patient_id=pid_uuid))

    # 2. Ensure DocumentReference exists for AI lab extraction
    stmt_doc = select(DocumentReference).where(DocumentReference.id == doc_uuid)
    res_doc = await session.execute(stmt_doc)
    if res_doc.scalar_one_or_none() is None:
        logger.info(" -> Seeding DocumentReference (Diagnostic Lab Slip)...")
        session.add(
            DocumentReference(
                id=doc_uuid,
                patient_id=pid_uuid,
                document_type="LAB_REPORT",
                uploaded_at=now - timedelta(hours=3),
                storage_ref="s3://nexa-care-demo/aarav_sharma_hba1c_report.pdf",
            )
        )

    # 3. Seed Vitals (BP, Sugar, Heart Rate) - source="manual"
    stmt_v = select(Vitals).where(Vitals.patient_id == pid_uuid, Vitals.type == "BP")
    if (await session.execute(stmt_v)).scalar_one_or_none() is None:
        logger.info(" -> Seeding Vitals (BP 130/85, Sugar 145, HR 78)...")
        session.add(
            Vitals(
                patient_id=pid_uuid,
                type="BP",
                value="130/85",
                unit="mmHg",
                recorded_at=now - timedelta(days=2),
                source="manual",
                risk_level="LOW_RISK",
            )
        )
        session.add(
            Vitals(
                patient_id=pid_uuid,
                type="SUGAR",
                value="145",
                unit="mg/dL",
                recorded_at=now - timedelta(days=2),
                source="manual",
                risk_level="MEDIUM_RISK",
            )
        )
        session.add(
            Vitals(
                patient_id=pid_uuid,
                type="HR",
                value="78",
                unit="bpm",
                recorded_at=now - timedelta(days=2),
                source="manual",
                risk_level="LOW_RISK",
            )
        )

    # 4. Seed Medication - source="manual"
    stmt_m = select(Medication).where(Medication.patient_id == pid_uuid, Medication.name == "Metformin")
    if (await session.execute(stmt_m)).scalar_one_or_none() is None:
        logger.info(" -> Seeding Medication (Metformin 500mg Twice daily)...")
        session.add(
            Medication(
                patient_id=pid_uuid,
                name="Metformin",
                strength="500mg",
                frequency="Twice daily",
                prescribed_at=now - timedelta(days=14),
                source="manual",
                risk_level="MEDIUM_RISK",
            )
        )

    # 5. Seed Allergy - source="manual", HIGH_RISK
    stmt_a = select(Allergy).where(Allergy.patient_id == pid_uuid, Allergy.allergen == "Penicillin")
    if (await session.execute(stmt_a)).scalar_one_or_none() is None:
        logger.info(" -> Seeding Allergy (Penicillin Severe - HIGH_RISK)...")
        session.add(
            Allergy(
                patient_id=pid_uuid,
                allergen="Penicillin",
                severity="Severe",
                source="manual",
                risk_level="HIGH_RISK",
            )
        )

    # 6. Seed Lab Result - source="ai_extracted"
    stmt_l = select(LabResult).where(LabResult.patient_id == pid_uuid, LabResult.test_name == "HbA1c")
    if (await session.execute(stmt_l)).scalar_one_or_none() is None:
        logger.info(" -> Seeding Lab Result (HbA1c 7.2% AI-extracted, confidence=0.96)...")
        session.add(
            LabResult(
                patient_id=pid_uuid,
                test_name="HbA1c",
                value="7.2",
                unit="%",
                reference_range="4.0-5.6 %",
                is_abnormal=True,
                recorded_at=now - timedelta(hours=2),
                source="ai_extracted",
                confidence=0.96,
                risk_level="HIGH_RISK",
                source_document_id=doc_uuid,
            )
        )

    # 7. Seed Timeline Events
    stmt_te = select(TimelineEvent).where(TimelineEvent.patient_id == pid_uuid)
    if len((await session.execute(stmt_te)).scalars().all()) == 0:
        logger.info(" -> Seeding Clinical Timeline Feed for Aarav Sharma...")
        session.add(
            TimelineEvent(
                patient_id=pid_uuid,
                event_type="ENCOUNTER",
                occurred_at=now - timedelta(days=14),
                source="manual",
                summary="Encounter recorded: Initial Type 2 Diabetes management checkup",
            )
        )
        session.add(
            TimelineEvent(
                patient_id=pid_uuid,
                event_type="MEDICATION",
                occurred_at=now - timedelta(days=14),
                source="manual",
                summary="Medication prescribed: Metformin 500mg (Twice daily)",
            )
        )
        session.add(
            TimelineEvent(
                patient_id=pid_uuid,
                event_type="ALLERGY",
                occurred_at=now - timedelta(days=14),
                source="manual",
                summary="Allergy recorded: Penicillin (Severe)",
            )
        )
        session.add(
            TimelineEvent(
                patient_id=pid_uuid,
                event_type="VITALS",
                occurred_at=now - timedelta(days=2),
                source="manual",
                summary="Vitals recorded: BP 130/85 mmHg, Blood Sugar 145 mg/dL, HR 78 bpm",
            )
        )
        session.add(
            TimelineEvent(
                patient_id=pid_uuid,
                event_type="DOCUMENT",
                occurred_at=now - timedelta(hours=3),
                source="manual",
                summary="Document uploaded: Diagnostic Lab Report (HbA1c Panel)",
            )
        )
        session.add(
            TimelineEvent(
                patient_id=pid_uuid,
                event_type="EXTRACTED_DATA_INGESTED",
                event_ref_id=doc_uuid,
                occurred_at=now - timedelta(hours=2),
                source="ai_extracted",
                summary="AI ingested HbA1c: 7.2 % (Confidence: 0.96)",
            )
        )

    await session.commit()
    logger.info(" ✅ Canonical Demo Patient Aarav Sharma seeded successfully.")


async def main() -> None:
    env = os.getenv("ENV", os.getenv("ENVIRONMENT", "development")).lower().strip()
    if env in {"prod", "production"}:
        raise RuntimeError(f"Refusing to seed demo patient in production environment ('{env}').")

    logger.info("==========================================================================")
    logger.info(" 🌱 NEXA CARE CANONICAL DEMO PATIENT SEEDER (Aarav Sharma)")
    logger.info("==========================================================================")
    engine = get_async_engine()
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        await seed_aarav_sharma(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
