"""Correction Dataset Logger (Workstream 5, Days 9–11).

Captures human steward corrections on AI-extracted observations into the
``field_corrections`` table for future model improvement and accuracy
reporting.

Privacy guarantee
-----------------
PII field values (``patient_name``, ``phone``, ``aadhaar_abha_id``, etc.)
are **redacted** before storage.  Only the field *name* is retained so that
accuracy statistics can be computed per category without exposing patient
data.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import FieldCorrection

logger = logging.getLogger("nexa_logger")

# ── PII field names whose values must be redacted ───────────────────────────
_PII_FIELD_NAMES = frozenset(
    {
        "patient_name",
        "phone",
        "aadhaar",
        "aadhaar_abha_id",
        "email",
        "dob",
        "nfc_uid",
        "bio_seed",
        "derived_alpha",
    }
)

_REDACTED = "[REDACTED]"


def _redact_value(field_name: str, value: str) -> str:
    """Redact the value if the field is a PII category."""
    if field_name.lower().strip() in _PII_FIELD_NAMES:
        return _REDACTED
    return value


async def log_correction(
    *,
    field_id: uuid.UUID,
    job_id: uuid.UUID,
    field_name: str,
    original_value: str,
    corrected_value: str,
    confidence: float,
    original_risk: str,
    document_type: str | None = None,
    corrected_by: str | None = None,
    db: AsyncSession,
) -> FieldCorrection:
    """Persist a single human correction with PII redaction.

    Returns the created :class:`FieldCorrection` row (not yet committed;
    the caller is responsible for committing the transaction).
    """
    redacted_original = _redact_value(field_name, original_value)
    redacted_corrected = _redact_value(field_name, corrected_value)

    fc = FieldCorrection(
        id=uuid.uuid4(),
        field_id=field_id,
        job_id=job_id,
        field_name=field_name,
        original_value=redacted_original,
        corrected_value=redacted_corrected,
        confidence=confidence,
        corrected_by=corrected_by,
        corrected_at=datetime.now(timezone.utc),
    )
    db.add(fc)

    logger.info(
        {
            "event": "field_correction_logged",
            "field_id": str(field_id),
            "job_id": str(job_id),
            "field_name": field_name,
            "confidence": confidence,
            "original_risk": original_risk,
            "document_type": document_type,
            "redacted": field_name.lower().strip() in _PII_FIELD_NAMES,
        }
    )

    return fc


async def export_corrections(
    db: AsyncSession,
    *,
    job_id: uuid.UUID | None = None,
    field_name: str | None = None,
) -> list[dict[str, Any]]:
    """Export correction records for future model training.

    Returns a list of dictionaries with the correction payload.  PII
    values are already redacted at write time so no further redaction
    is needed here.  Optional filters narrow the result set.
    """
    stmt = select(FieldCorrection).order_by(FieldCorrection.corrected_at)

    if job_id is not None:
        stmt = stmt.where(FieldCorrection.job_id == job_id)
    if field_name is not None:
        stmt = stmt.where(FieldCorrection.field_name == field_name)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {
            "field_name": r.field_name,
            "original_value": r.original_value,
            "corrected_value": r.corrected_value,
            "original_confidence": r.confidence,
            "corrected_by": r.corrected_by,
            "corrected_at": r.corrected_at.isoformat() if r.corrected_at else None,
        }
        for r in rows
    ]
