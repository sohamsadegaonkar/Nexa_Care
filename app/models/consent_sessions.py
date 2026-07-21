"""Consent session ORM model for Nexa Care v1.0.

Matches the ``consent_sessions`` table defined in
``alembic/versions/20260705_nexa_v1_core_identity_consent.py``.
This is the live, revocable token issued alongside each
``consent_ledger`` entry (see ``consent_ledger.py``); token validation
reads from this table rather than the immutable ledger.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConsentSession(Base):
    """Live, revocable consent token tied to a consent ledger entry."""

    __tablename__ = "consent_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
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
    consent_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    consent_assurance: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hospital_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    clinician_id: Mapped[str | None] = mapped_column(Text, nullable=True)
