"""NFC-card identity resolution for the Nexa Care V2 identity layer.

The resolver is the single source of truth for translating a physical NFC card
UID into the masked patient identity used by authorized backend services.
Resolution fails closed: only cards whose lifecycle state is exactly
``active`` may yield a patient UUID.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, validate_call
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus
from app.observability.audit_ledger import append_audit_log_or_503

logger = logging.getLogger("nexa_logger")

CardUID = Annotated[str, Field(min_length=1, max_length=128)]

_CARD_FORBIDDEN_DETAIL = "NFC card is not active or is not authorized."
_CARD_DB_UNAVAILABLE_DETAIL = "NFC card resolution is temporarily unavailable."


class CardResolutionInput(BaseModel):
    """Strict Pydantic V2 input contract for card resolution."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    card_uid: CardUID


class ReportLostCardInput(BaseModel):
    """Strict Pydantic V2 input contract for reporting a lost card."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    card_uid: CardUID
    reported_by: UUID


class CardStatusUpdateResult(BaseModel):
    """Strict Pydantic V2 output contract for card status mutations."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    patient_id: UUID
    status: NFCCardStatus


class CardResolutionService:
    """Resolve and manage NFC card identity bindings.

    The service deliberately does not return patient identity for any card
    state except ``active``. Lost, revoked, replaced, unknown, or malformed
    registry states are all authorization failures because card possession is
    no longer sufficient proof that the holder may access the mapped patient.
    """

    def __init__(self, db: AsyncSession) -> None:
        """Create a resolver bound to a request-scoped async database session."""

        self._db = db

    @validate_call(
        config=ConfigDict(strict=True, arbitrary_types_allowed=True),
        validate_return=True,
    )
    async def resolve_card(self, card_uid: CardUID) -> UUID:
        """Return the masked patient UUID for an active NFC card.

        Security rationale: card resolution is an authorization boundary. The
        method fails closed and raises HTTP 403 for unknown cards or any card
        state other than ``active``. It must never return ``patient_id`` for a
        lost, revoked, or replaced card because that would allow a stale
        physical credential to reassemble identity context.
        """

        request = CardResolutionInput(card_uid=card_uid)
        row = await self._load_card_by_uid(request.card_uid)

        if row is None:
            logger.warning(json.dumps({
                "event": "nfc_card_resolution_denied",
                "reason": "card_not_found",
            }))
            raise self._forbidden()

        if row.status != NFCCardStatus.ACTIVE.value:
            logger.warning(json.dumps({
                "event": "nfc_card_resolution_denied",
                "reason": "inactive_card",
                "status": row.status,
                "patient_id": str(row.patient_id),
            }))
            raise self._forbidden()

        return row.patient_id

    @validate_call(
        config=ConfigDict(strict=True, arbitrary_types_allowed=True),
        validate_return=True,
    )
    async def report_lost_card(
        self,
        card_uid: CardUID,
        reported_by: UUID,
    ) -> CardStatusUpdateResult:
        """Mark a card as reported lost and append an immutable audit event.

        The audit ledger is written before committing the state change, so a
        ledger outage aborts the mutation instead of allowing an unaudited card
        lifecycle event. The raw card UID is not written to the audit payload;
        the event is keyed by the masked patient UUID and actor UUID.
        """

        request = ReportLostCardInput(card_uid=card_uid, reported_by=reported_by)
        row = await self._load_card_by_uid(request.card_uid, lock_for_update=True)

        if row is None:
            logger.warning(json.dumps({
                "event": "nfc_card_report_lost_denied",
                "reason": "card_not_found",
                "reported_by": str(request.reported_by),
            }))
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="NFC card registry entry was not found.",
            )

        await append_audit_log_or_503(
            actor_uid=str(request.reported_by),
            event_type="NFC_CARD_REPORTED_LOST",
            target_id=str(row.patient_id),
            status="STARTED",
        )

        try:
            row.status = NFCCardStatus.REPORTED_LOST.value
            await self._db.commit()
        except SQLAlchemyError as exc:
            await self._db.rollback()
            logger.critical(json.dumps({
                "event": "nfc_card_report_lost_db_error",
                "patient_id": str(row.patient_id),
                "reported_by": str(request.reported_by),
                "exception": str(exc),
            }))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NFC card status update failed.",
            ) from exc

        await append_audit_log_or_503(
            actor_uid=str(request.reported_by),
            event_type="NFC_CARD_REPORTED_LOST",
            target_id=str(row.patient_id),
            status="SUCCESS",
        )

        return CardStatusUpdateResult(
            patient_id=row.patient_id,
            status=NFCCardStatus.REPORTED_LOST,
        )

    async def _load_card_by_uid(
        self,
        card_uid: str,
        *,
        lock_for_update: bool = False,
    ) -> NFCCardRegistry | None:
        """Load one card row, translating DB failures into secure 503 errors."""

        stmt = select(NFCCardRegistry).where(NFCCardRegistry.card_uid == card_uid)
        if lock_for_update:
            stmt = stmt.with_for_update()

        try:
            result = await self._db.execute(stmt)
        except SQLAlchemyError as exc:
            logger.critical(json.dumps({
                "event": "nfc_card_registry_db_error",
                "exception": str(exc),
                "action": "raising_503_fail_closed",
            }))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_CARD_DB_UNAVAILABLE_DETAIL,
            ) from exc

        return result.scalar_one_or_none()

    @staticmethod
    def _forbidden() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_CARD_FORBIDDEN_DETAIL,
        )
