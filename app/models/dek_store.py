"""SQLAlchemy model for per-patient Data Encryption Key (DEK) storage."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Integer, LargeBinary, String, DateTime, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PatientDEKStore(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Stores KEK-wrapped DEKs for each patient.

    The DEK is never stored in plaintext. It is wrapped using the system-wide
    KEK before being persisted here. Supports versioning and rotation.
    """

    __tablename__ = "patient_dek_store"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_iv: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="AES-256-GCM")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("patient_id", "dek_version", name="uq_patient_dek_version"),
    )
