"""Consent ledger ORM model for Nexa Care v1.0.

Matches the ``consent_ledger`` table defined in
``alembic/versions/20260705_nexa_v1_core_identity_consent.py``.
This is the immutable record of every consent grant (routine or
break-glass); ``consent_sessions`` (see ``consent_sessions.py``) tracks
the live, revocable token issued alongside each ledger entry.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConsentLedger(Base):
    """Immutable record of a consent grant."""

    __tablename__ = "consent_ledger"

    consent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    patient_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.patient_uuid"),
        nullable=False,
    )
    hospital_id: Mapped[str] = mapped_column(Text, nullable=False)
    clinician_id: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    consent_assurance: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    digital_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_change_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
