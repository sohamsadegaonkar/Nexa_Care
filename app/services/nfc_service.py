"""NFC card lifecycle service for Nexa Care V2 Phase B.

Every status mutation writes an ``NFCCardEvent`` in the same database
transaction so the registry and ledger never diverge.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nfc import (
    NFCCardEvent,
    NFCCardEventType,
    NFCCardRegistry,
    NFCCardSourceType,
    NFCCardStatus,
    NFCCardType,
)


class NFCCardNotFoundError(Exception):
    """Raised when no registry row matches the supplied card UID."""


class NFCCardConflictError(Exception):
    """Raised when a card UID is already registered."""


class NFCCardStateError(Exception):
    """Raised when a lifecycle transition is invalid for the current status."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _append_card_event(
    session: AsyncSession,
    *,
    card: NFCCardRegistry,
    event_type: NFCCardEventType,
    actor_uid: uuid.UUID | None,
    details: dict[str, Any] | None = None,
) -> NFCCardEvent:
    """Record a lifecycle event in the current transaction."""

    event = NFCCardEvent(
        card_id=card.id,
        event_type=event_type.value,
        actor_uid=actor_uid,
        details=details,
    )
    session.add(event)
    return event


async def issue_hospital_card(
    session: AsyncSession,
    *,
    card_uid: str,
    hospital_id: uuid.UUID,
    actor_uid: uuid.UUID,
) -> NFCCardRegistry:
    """Register a blank hospital-issued card awaiting patient binding."""

    card = NFCCardRegistry(
        card_uid=card_uid,
        status=NFCCardStatus.PENDING_BINDING.value,
        source_type=NFCCardSourceType.HOSPITAL.value,
        hospital_id=hospital_id,
        card_type=NFCCardType.PATIENT_CARD.value,
    )
    session.add(card)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise NFCCardConflictError(f"card_uid already registered: {card_uid}") from exc

    _append_card_event(
        session,
        card=card,
        event_type=NFCCardEventType.CARD_ISSUED,
        actor_uid=actor_uid,
        details={
            "hospital_id": str(hospital_id),
            "status": NFCCardStatus.PENDING_BINDING.value,
        },
    )
    await session.commit()
    await session.refresh(card)
    return card


async def activate_card(
    session: AsyncSession,
    *,
    card_uid: str,
    patient_id: uuid.UUID,
    provider_uid: uuid.UUID,
) -> NFCCardRegistry:
    """Bind a patient to a card and transition it to ACTIVE."""

    result = await session.execute(
        select(NFCCardRegistry).where(NFCCardRegistry.card_uid == card_uid)
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise NFCCardNotFoundError(card_uid)

    if card.status != NFCCardStatus.PENDING_BINDING.value:
        raise NFCCardStateError(
            f"card {card_uid} cannot be activated from status {card.status}"
        )

    previous_status = card.status
    card.patient_id = patient_id
    card.status = NFCCardStatus.ACTIVE.value
    card.activated_at = _utcnow()
    card.activated_by = provider_uid

    _append_card_event(
        session,
        card=card,
        event_type=NFCCardEventType.CARD_BOUND,
        actor_uid=provider_uid,
        details={
            "patient_id": str(patient_id),
            "previous_status": previous_status,
        },
    )
    _append_card_event(
        session,
        card=card,
        event_type=NFCCardEventType.CARD_ACTIVATED,
        actor_uid=provider_uid,
        details={
            "patient_id": str(patient_id),
            "activated_at": card.activated_at.isoformat(),
        },
    )
    await session.commit()
    await session.refresh(card)
    return card


async def resolve_active_card_patient(
    session: AsyncSession,
    *,
    card_uid: str,
) -> uuid.UUID | None:
    """Return the bound patient id only when the card is ACTIVE."""

    result = await session.execute(
        select(NFCCardRegistry).where(NFCCardRegistry.card_uid == card_uid)
    )
    card = result.scalar_one_or_none()
    if card is None or card.status != NFCCardStatus.ACTIVE.value:
        return None
    if card.patient_id is None:
        return None
    return card.patient_id
