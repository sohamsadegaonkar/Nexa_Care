"""Encrypted patient profile storage.

This model stores per-patient profile data where all PII fields contain
serialized envelope ciphertext (EncryptedField.serialize() format), never
plaintext values.  The patient_id is the primary key and a foreign key to
patients.patient_uuid.

This is a SEPARATE bounded context from NexaVault (extraction identity shard)
and from the patients table (minimal identity record with plaintext PII barrier).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PatientProfile(Base):
    """Encrypted patient profile — full_name and date_of_birth only.

    Columns ``full_name_encrypted`` and ``date_of_birth_encrypted`` contain
    serialized AES-256-GCM envelope ciphertext in the format produced by
    ``EncryptedField.serialize()`` (base64(iv+ciphertext):dek_version).
    They must NEVER contain plaintext PII.
    """

    __tablename__ = "patient_profiles"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.patient_uuid", ondelete="RESTRICT"),
        primary_key=True,
    )
    full_name_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    date_of_birth_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
