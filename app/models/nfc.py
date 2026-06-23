"""NFC card lifecycle ORM models for Nexa Care V2 Phase B.

The registry tracks hardware-agnostic card state; every status transition
is mirrored in ``nfc_card_event`` so lifecycle history is append-only.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NFCCardStatus(str, enum.Enum):
    """Strict lifecycle states for an NFC patient card."""

    PENDING_BINDING = "PENDING_BINDING"
    ORDERED = "ORDERED"
    SHIPPED = "SHIPPED"
    ACTIVE = "ACTIVE"
    LOST = "LOST"
    REPLACED = "REPLACED"
    DEACTIVATED = "DEACTIVATED"


class NFCCardSourceType(str, enum.Enum):
    """Onboarding channel that issued the card."""

    HOSPITAL = "HOSPITAL"
    D2C = "D2C"


class NFCCardType(str, enum.Enum):
    """Physical card product variant."""

    PATIENT_CARD = "PATIENT_CARD"


class NFCCardEventType(str, enum.Enum):
    """Immutable ledger event types for card lifecycle changes."""

    CARD_ORDERED = "CARD_ORDERED"
    CARD_ISSUED = "CARD_ISSUED"
    CARD_BOUND = "CARD_BOUND"
    CARD_ACTIVATED = "CARD_ACTIVATED"
    CARD_LOST = "CARD_LOST"
    CARD_REPLACED = "CARD_REPLACED"
    CARD_DEACTIVATED = "CARD_DEACTIVATED"


class NFCCardRegistry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Hardware-agnostic NFC card registry entry."""

    __tablename__ = "nfc_card_registry"

    card_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("nexa_vault.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_registry.id", ondelete="SET NULL"),
        nullable=True,
    )
    card_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NFCCardType.PATIENT_CARD.value,
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    activated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    previous_card_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    replaced_by_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    events: Mapped[list["NFCCardEvent"]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("card_uid", name="uq_nfc_card_registry_card_uid"),
        Index("ix_nfc_card_registry_card_uid", "card_uid"),
        Index("ix_nfc_card_registry_status", "status"),
        Index("ix_nfc_card_registry_patient_id", "patient_id"),
        Index("ix_nfc_card_registry_hospital_id", "hospital_id"),
    )


class NFCCardEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only lifecycle ledger for a single NFC card."""

    __tablename__ = "nfc_card_event"

    card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("nfc_card_registry.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    card: Mapped[NFCCardRegistry] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_nfc_card_event_card_id", "card_id"),
        Index("ix_nfc_card_event_event_type", "event_type"),
        Index("ix_nfc_card_event_created_at", "created_at"),
    )
