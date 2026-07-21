"""Extracted Data Ingestion Service for Nexa Care V2 (Workstream 3 & 4 seam).

Routes approved ExtractedField objects from AI pipeline jobs into structured
patient record sub-models (Vitals, Medication, LabResult, Allergy) with full
provenance enforcement, idempotency per job, and hard-auditing.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extracted_field import ExtractedField
from app.models.patient_records import (
    LabResult,
    TimelineEvent,
    Vitals,
)
from app.models.pipeline import PipelineCommit
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")

VALID_RISK_LEVELS = {"LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"}


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
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "INVALID_UUID"}
        ) from exc


async def ingest_extracted_fields(
    patient_id: str,
    job_id: str,
    approved_fields: list[ExtractedField],
    db: AsyncSession,
    committed_by: str,
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

    stmt_existing = (
        select(TimelineEvent)
        .where(
            TimelineEvent.patient_id == pid_uuid,
            TimelineEvent.event_ref_id == job_uuid,
            TimelineEvent.source == "ai_extracted",
        )
        .limit(1)
    )
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
        if field.field_id is None or field.job_id is None:
            raise HTTPException(
                status_code=400,
                detail="Provenance error: field_id and job_id are required",
            )
        if _parse_uuid(field.job_id) != job_uuid:
            raise HTTPException(
                status_code=409,
                detail="Extracted field job_id does not match commit job",
            )
        # 2. Status adjudication enforcement: reject unreviewed or rejected AI output
        if str(field.status).lower() not in {"approved", "edited"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Safety violation: field '{field.field_name}' has unreviewed or rejected status '{field.status}'. Only explicitly approved or edited fields may be ingested into clinical records.",
            )

        # 3. Provenance enforcement (Invariant 3)
        if (
            field.confidence is None
            or not isinstance(field.confidence, (int, float))
            or not (0.0 <= field.confidence <= 1.0)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provenance error: field {field.field_name} lacks valid numeric confidence.",
            )
        if not field.risk_level or str(field.risk_level).strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provenance error: field {field.field_name} lacks risk_level metadata.",
            )
        if str(field.risk_level) not in VALID_RISK_LEVELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provenance error: field {field.field_name} has invalid risk_level metadata.",
            )

        if not field.source_document_id:
            raise HTTPException(
                status_code=400,
                detail="Provenance error: source_document_id is required",
            )
        doc_id_str = field.source_document_id
        doc_uuid = _parse_uuid(doc_id_str)

        fname = field.field_name.lower().strip()
        val = field.corrected_value or field.normalized_value or field.raw_value or ""

        # Route by field_name
        if fname in {
            "bp",
            "sugar",
            "heart_rate",
            "blood_pressure",
            "temp",
            "temperature",
            "spo2",
            "sp_o2",
            "systolic_bp",
            "diastolic_bp",
        }:
            if not field.units:
                raise HTTPException(
                    status_code=409,
                    detail="Clinically material vital units must be adjudicated before commit",
                )
            v = Vitals(
                patient_id=pid_uuid,
                type=field.field_name.upper(),
                value=val,
                unit=field.units,
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
            raise HTTPException(
                status_code=409,
                detail="Medication extraction requires structured strength and frequency adjudication",
            )
        elif fname in {"allergy", "allergen"}:
            raise HTTPException(
                status_code=409,
                detail="Allergy extraction requires structured allergen and severity adjudication",
            )
        elif fname in {"lab_result", "hba1c", "glucose", "fasting_glucose"}:
            if not field.units:
                raise HTTPException(
                    status_code=409,
                    detail="Clinically material lab units must be adjudicated before commit",
                )
            reference = None
            if field.validation_result is not None:
                reference = getattr(field.validation_result, "reference_range", None)
            if reference is None:
                raise HTTPException(
                    status_code=409,
                    detail="Lab reference range must be adjudicated before commit",
                )
            lab = LabResult(
                patient_id=pid_uuid,
                test_name=field.field_name,
                value=val,
                unit=field.units,
                reference_range=str(reference),
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
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Unsupported canonical field type: {field.field_name}",
            )

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
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            actor_uid=str(patient_id),
            event_type="EXTRACTED_DATA_INGESTED",
            target_id=str(field.field_id),
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
        committed_by=committed_by,
        ingested_count=len(approved_fields),
    )
    db.add(pc)
    await db.flush()

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
