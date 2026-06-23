"""NFC card lifecycle API routes (Phase B — Identity Bridge)."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.core.redis import get_redis_client
from app.models.nfc import NFCCardRegistry
from app.models.provider_context import ProviderContext
from app.services.nfc_service import (
    NFCCardConflictError,
    NFCCardNotFoundError,
    NFCCardStateError,
    activate_card,
    issue_hospital_card,
    resolve_active_card_patient,
)

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/nfc", tags=["nfc"])

_NFC_RESOLVE_RATE_LIMIT = 10
_NFC_RESOLVE_RATE_WINDOW_SECONDS = 60
_NFC_RESOLVE_RATE_KEY_PREFIX = "nfc_resolve_rl:"


class IssueCardRequest(BaseModel):
    card_uid: str = Field(..., min_length=1, max_length=128)


class ActivateCardRequest(BaseModel):
    card_uid: str = Field(..., min_length=1, max_length=128)
    patient_id: UUID


class ResolveCardRequest(BaseModel):
    card_uid: str = Field(..., min_length=1, max_length=128)


class CardResponse(BaseModel):
    id: UUID
    card_uid: str
    status: str
    source_type: str
    hospital_id: UUID | None = None
    patient_id: UUID | None = None


class ResolveCardResponse(BaseModel):
    patient_id: UUID


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _enforce_nfc_resolve_rate_limit(client_ip: str) -> None:
    """Redis-backed sliding counter to deter UID enumeration on resolve."""

    redis = get_redis_client()
    key = f"{_NFC_RESOLVE_RATE_KEY_PREFIX}{client_ip}"
    try:
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, _NFC_RESOLVE_RATE_WINDOW_SECONDS)
        if count > _NFC_RESOLVE_RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many resolve attempts",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "nfc_resolve_rate_limit_unavailable",
            extra={"client_ip": client_ip, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Request could not be completed",
        ) from exc


def _card_to_response(card: NFCCardRegistry) -> CardResponse:
    return CardResponse(
        id=card.id,
        card_uid=card.card_uid,
        status=card.status,
        source_type=card.source_type,
        hospital_id=card.hospital_id,
        patient_id=card.patient_id,
    )


@router.post("/issue", response_model=CardResponse)
async def issue_card(
    body: IssueCardRequest,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> CardResponse:
    """Issue a hospital card in PENDING_BINDING state."""

    try:
        card = await issue_hospital_card(
            db,
            card_uid=body.card_uid,
            hospital_id=provider.hospital.hospital_id,
            actor_uid=provider.provider.provider_id,
        )
    except NFCCardConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Card UID already registered",
        ) from None

    return _card_to_response(card)


@router.post("/activate", response_model=CardResponse)
async def activate_nfc_card(
    body: ActivateCardRequest,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> CardResponse:
    """Bind a patient to a card and activate it."""

    try:
        card = await activate_card(
            db,
            card_uid=body.card_uid,
            patient_id=body.patient_id,
            provider_uid=provider.provider.provider_id,
        )
    except NFCCardNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found",
        ) from None
    except NFCCardStateError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Card cannot be activated in its current state",
        ) from None

    return _card_to_response(card)


@router.post("/resolve", response_model=ResolveCardResponse)
async def resolve_card(
    body: ResolveCardRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ResolveCardResponse:
    """Simulate an NFC hardware tap — returns patient_id only for ACTIVE cards."""

    _enforce_nfc_resolve_rate_limit(_client_ip(request))

    patient_id = await resolve_active_card_patient(db, card_uid=body.card_uid)
    if patient_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found",
        )

    return ResolveCardResponse(patient_id=patient_id)
