"""SQLAlchemy model for patient push tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

class PatientPushToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Stores Expo push tokens for patient devices."""

    __tablename__ = "patient_push_tokens"

    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    expo_push_token: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # 'ios' | 'android'
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("patient_id", "expo_push_token", name="uq_patient_push_token"),
    )

class PushRequestLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Durable record of push approval requests and their outcomes."""

    __tablename__ = "push_request_log"

    request_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True, index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending, approved, denied, timeout

    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
