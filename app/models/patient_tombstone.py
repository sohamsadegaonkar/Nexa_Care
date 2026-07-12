"""Patient tombstone ORM model for Nexa Care patient-merge workflow.

A tombstone marks a patient record as merged into a canonical record
(Section 9 of the Nexa Care v1.0 Architecture). Scanning a card tied to
``old_patient_uuid`` should redirect callers to ``canonical_patient_uuid``
rather than resolving the old, now-retired identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PatientTombstone(Base):
    """Record of a patient identity merged into a canonical patient record."""

    __tablename__ = "patient_tombstones"

    tombstone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    old_patient_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_patient_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.patient_uuid"),
        nullable=False,
    )
    merged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    merged_by: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("old_patient_uuid", name="uq_patient_tombstones_old_patient_uuid"),
    )