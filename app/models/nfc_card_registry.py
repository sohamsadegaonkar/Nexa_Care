"""NFC card registry ORM model for Nexa Care identity resolution.

This table is intentionally limited to card state and the masked patient
identifier. It must never contain patient PII or clinical facts; those remain
separated in the vault and clinical shards.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NFCCardStatus(str, enum.Enum):
    """Allowed lifecycle states for a physical NFC card."""

    ACTIVE = "active"
    REPORTED_LOST = "reported_lost"
    REVOKED = "revoked"
    REPLACED = "replaced"


class NFCCardRegistry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Mapping between a physical NFC card UID and a masked patient identity.

    ``patient_id`` is the masked internal ID used to bridge the identity layer
    to authorized backend workflows. No names, DOBs, phone numbers, diagnoses,
    or other PII/clinical data are allowed in this table.
    """

    __tablename__ = "nfc_card_registry"

    card_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NFCCardStatus.ACTIVE.value,
        server_default=NFCCardStatus.ACTIVE.value,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    issued_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("card_uid", name="uq_nfc_card_registry_card_uid"),
        CheckConstraint(
            "status IN ('active', 'reported_lost', 'revoked', 'replaced')",
            name="ck_nfc_card_registry_status",
        ),
        Index("ix_nfc_card_registry_card_uid", "card_uid"),
        Index("ix_nfc_card_registry_patient_id", "patient_id"),
        Index("ix_nfc_card_registry_status", "status"),
    )
