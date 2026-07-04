"""NFC card resolution routes for Nexa Care V2.

Resolves a physical card UID to a masked patient identifier for the
provider-facing scanner flow. No clinical data is returned here; the
caller still needs a separate consent grant to read the patient record.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.core.redis import get_redis_client
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.services.card_resolution_service import CardResolutionService
from app.services.card_redirect_service import CardRedirectService

router = APIRouter(prefix="/api/v2/nfc", tags=["nfc"])


class NFCResolveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    card_uid: str = Field(..., min_length=1, max_length=128)

    def __post_init__(self):
        # Sanitize card_uid
        self.card_uid = self.card_uid.strip().upper()


class NFCResolveResponse(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    patient_id: UUID
    canonical_patient_id: UUID | None = None
    is_redirected: bool = False


@router.post("/resolve", response_model=NFCResolveResponse)
async def resolve_nfc_card(
    payload: NFCResolveRequest,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> NFCResolveResponse:
    """Resolve a card UID to the masked patient ID it is bound to.

    Fail-closed: unknown, lost, or revoked cards raise 403, and any
    unexpected resolution error raises 503.
    """

    # Rate limiting: 30 NFC scans per provider per minute
    try:
        redis = get_redis_client()
        rate_key = f"nfc_resolve_rate:{provider.provider_id}"
        current = await redis.incr(rate_key)
        if current == 1:
            await redis.expire(rate_key, 60)
        if current > 30:
            raise HTTPException(status_code=429, detail="Too many NFC scan attempts")
    except Exception:
        pass

    resolver = CardResolutionService(db)
    redirect_service = CardRedirectService(db)

    try:
        patient_id = await resolver.resolve_card(payload.card_uid)

        # Tombstone redirect check (Section 9)
        redirect_result = await redirect_service.resolve_card_with_redirect(payload.card_uid)

        if redirect_result.get("is_redirected"):
            return NFCResolveResponse(
                patient_id=patient_id,
                canonical_patient_id=redirect_result.get("canonical_patient_uuid"),
                is_redirected=True,
            )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NFC resolution service is temporarily unavailable.",
        ) from exc

    # Audit every NFC resolution attempt
    await append_audit_log(
        actor_uid=provider.provider_id,
        event_type="NFC_CARD_RESOLVED",
        target_id=str(patient_id),
        status="SUCCESS",
        metadata={
            "card_uid": payload.card_uid[:8] + "...",  # partial for privacy
            "is_redirected": redirect_result.get("is_redirected", False)
        }
    )

    return NFCResolveResponse(patient_id=patient_id, is_redirected=False)
