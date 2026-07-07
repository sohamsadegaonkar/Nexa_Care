"""Extracted Data Ingestion Service for Nexa Care V2 (Workstream 3 & 4 seam).

Routes approved ExtractedField objects from AI pipeline jobs into structured
patient record sub-models (Vitals, Medication, LabResult, Allergy) with full
provenance enforcement, idempotency per job, and hard-auditing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extracted_field import ExtractedField
from app.models.patient_records import (
    Allergy,
    LabResult,
    Medication,
    TimelineEvent,
    Vitals,
)
from app.models.pipeline import PipelineCommit
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")


class IngestionResult(BaseModel):
    """Result summary returned after ingesting a pipeline extraction job."""
    job_id: str
    patient_id: str
    ingested_count: int
    vitals_created: int
    medications_created: int
    labs_created: int
    allergies_created: int
    timeline_events_created: int


def _parse_uuid(id_str: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(id_str))
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_DNS, str(id_str))


async def ingest_extracted_fields(
    patient_id: str,
    job_id: str,
    approved_fields: list[ExtractedField],
    db: AsyncSession,
) -> IngestionResult:
    """Ingest approved AI extraction fields into patient sub-models with full provenance."""
    pid_uuid = _parse_uuid(patient_id)
    job_uuid = _parse_uuid(job_id)

    # 1. Idempotency Check: check dedicated PipelineCommit marker or fallback TimelineEvent
    stmt_pc = select(PipelineCommit).where(PipelineCommit.job_id == job_uuid).limit(1)
    res_pc = await db.execute(stmt_pc)
    if res_pc.scalar_one_or_none() is not None:
        return IngestionResult(
            job_id=job_id,
            patient_id=patient_id,
            ingested_count=0,
            vitals_created=0,
            medications_created=0,
            labs_created=0,
            allergies_created=0,
            timeline_events_created=0,
        )

    stmt_existing = select(TimelineEvent).where(
        TimelineEvent.patient_id == pid_uuid,
        TimelineEvent.event_ref_id == job_uuid,
        TimelineEvent.source == "ai_extracted",
    ).limit(1)
    res_existing = await db.execute(stmt_existing)
    if res_existing.scalar_one_or_none() is not None:
        return IngestionResult(
            job_id=job_id,
            patient_id=patient_id,
            ingested_count=0,
            vitals_created=0,
            medications_created=0,
            labs_created=0,
            allergies_created=0,
            timeline_events_created=0,
        )

    vitals_cnt = 0
    meds_cnt = 0
    labs_cnt = 0
    allergies_cnt = 0
    te_cnt = 0

    now = datetime.now(timezone.utc)

    for field in approved_fields:
        # 2. Status adjudication enforcement: reject unreviewed or rejected AI output
        if str(field.status).lower() not in {"auto_approved", "approved", "edited"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Safety violation: field '{field.field_name}' has unreviewed or rejected status '{field.status}'. Only auto_approved, approved, or edited fields may be ingested into clinical records.",
            )

        # 3. Provenance enforcement (Invariant 3)
        if field.confidence is None or not isinstance(field.confidence, (int, float)) or not (0.0 <= field.confidence <= 1.0):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provenance error: field {field.field_name} lacks valid numeric confidence.",
            )
        if not field.risk_level or str(field.risk_level).strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provenance error: field {field.field_name} lacks risk_level metadata.",
            )

        doc_id_str = field.source_document_id or job_id
        doc_uuid = _parse_uuid(doc_id_str)

        fname = field.field_name.lower().strip()
        val = field.corrected_value or field.normalized_value or field.raw_value or ""

        # Route by field_name
        if fname in {"bp", "sugar", "heart_rate", "blood_pressure", "temp", "temperature", "spo2", "sp_o2", "systolic_bp", "diastolic_bp"}:
            unit_map = {"bp": "mmHg", "blood_pressure": "mmHg", "sugar": "mg/dL", "heart_rate": "bpm", "temp": "C", "temperature": "C", "spo2": "%", "sp_o2": "%"}
            v = Vitals(
                patient_id=pid_uuid,
                type=field.field_name.upper(),
                value=val,
                unit=unit_map.get(fname, field.normalized_value or ""),
                recorded_at=now,
                source="ai_extracted",
                confidence=float(field.confidence),
                risk_level=str(field.risk_level),
                source_document_id=doc_uuid,
            )
            db.add(v)
            vitals_cnt += 1
            target_model = "Vitals"
        elif fname in {"medication", "prescription", "drug", "rx"}:
            m = Medication(
                patient_id=pid_uuid,
                name=val,
                strength=field.normalized_value or "Standard",
                frequency="As prescribed",
                prescribed_at=now,
                source="ai_extracted",
                confidence=float(field.confidence),
                risk_level=str(field.risk_level),
                source_document_id=doc_uuid,
            )
            db.add(m)
            meds_cnt += 1
            target_model = "Medication"
        elif fname in {"allergy", "allergen"}:
            # Enforce HIGH_RISK per WS5 rules
            a = Allergy(
                patient_id=pid_uuid,
                allergen=val,
                severity="Severe",
                source="ai_extracted",
                confidence=float(field.confidence),
                risk_level="HIGH_RISK",
                source_document_id=doc_uuid,
            )
            db.add(a)
            allergies_cnt += 1
            target_model = "Allergy"
        else:
            lab = LabResult(
                patient_id=pid_uuid,
                test_name=field.field_name,
                value=val,
                unit=field.normalized_value or "",
                reference_range="Standard",
                is_abnormal=str(field.risk_level) in {"HIGH_RISK", "CRITICAL_RISK"},
                recorded_at=now,
                source="ai_extracted",
                confidence=float(field.confidence),
                risk_level=str(field.risk_level),
                source_document_id=doc_uuid,
            )
            db.add(lab)
            labs_cnt += 1
            target_model = "LabResult"

        # Create TimelineEvent for ingested field
        te = TimelineEvent(
            patient_id=pid_uuid,
            event_type="EXTRACTED_DATA_INGESTED",
            event_ref_id=job_uuid,
            occurred_at=now,
            source="ai_extracted",
            summary=f"AI ingested {field.field_name}: {val} (Confidence: {field.confidence})",
        )
        db.add(te)
        te_cnt += 1

        # Hard-audit EXTRACTED_DATA_INGESTED
        await append_audit_log_or_503(
            actor_uid=str(patient_id),
            event_type="EXTRACTED_DATA_INGESTED",
            target_id=str(field.field_id or uuid.uuid4()),
            status="SUCCESS",
            metadata={
                "job_id": job_id,
                "field_name": field.field_name,
                "target_model": target_model,
                "confidence": field.confidence,
                "risk_level": field.risk_level,
            },
        )

    pc = PipelineCommit(
        job_id=job_uuid,
        patient_id=pid_uuid,
        committed_at=now,
        committed_by=str(patient_id),
        ingested_count=len(approved_fields),
    )
    db.add(pc)
    await db.commit()

    return IngestionResult(
        job_id=job_id,
        patient_id=patient_id,
        ingested_count=len(approved_fields),
        vitals_created=vitals_cnt,
        medications_created=meds_cnt,
        labs_created=labs_cnt,
        allergies_created=allergies_cnt,
        timeline_events_created=te_cnt,
    )
