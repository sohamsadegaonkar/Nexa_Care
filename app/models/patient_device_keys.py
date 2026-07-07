"""SQLAlchemy model for patient enrolled hardware device keys."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PatientDeviceKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Stores hardware-enrolled public keys (ECDSA P-256) for patients.

    Only public keys are stored server-side — never private keys.
    """

    __tablename__ = "patient_device_keys"

    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    device_public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    key_algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="ECDSA-P256")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("patient_id", "device_public_key", name="uq_patient_device_public_key"),
    )
