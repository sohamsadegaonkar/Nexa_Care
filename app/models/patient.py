"""Patient ORM model for Nexa Care core identity.

Matches the ``patients`` table defined in
``alembic/versions/20260705_nexa_v1_core_identity_consent.py``.
"""

from __future__ import annotations

import uuid
import secrets
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _default_public_patient_id() -> str:
    return "NC-" + secrets.token_hex(12).upper()


class Patient(Base):
    """Core patient identity record."""

    __tablename__ = "patients"

    patient_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    # Public discovery identifier.  It is intentionally opaque and is never
    # an authorization credential or a replacement for patient_uuid.
    public_patient_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        default=_default_public_patient_id,
        server_default=text("'NC-' || upper(encode(gen_random_bytes(12), 'hex'))"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    consent_assurance_policy: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="STANDARD",
    )
    dek_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
