"""NFC card resolution routes for Nexa Care V2.

Resolves a physical card UID to a masked patient identifier for the
provider-facing scanner flow. No clinical data is returned here; the
caller still needs a separate consent grant to read the patient record.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.core.redis import get_async_redis_client
from app.core.rate_limiter import atomic_fixed_window
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.services.card_resolution_service import CardResolutionService
from app.services.card_redirect_service import (
    CardRedirectService,
    TombstoneIntegrityError,
)

router = APIRouter(prefix="/api/v2/nfc", tags=["nfc"])


class NFCResolveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    card_uid: str = Field(..., min_length=1, max_length=128)

    @field_validator("card_uid", mode="before")
    @classmethod
    def normalize_card_uid(cls, value: object) -> object:
        """Normalize physical card UIDs before lookup."""

        if isinstance(value, str):
            return value.strip().upper()
        return value


class NFCResolveResponse(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    patient_id: str
    canonical_patient_id: str | None = None
    is_redirected: bool = False

    @field_validator("patient_id", "canonical_patient_id", mode="before")
    @classmethod
    def stringify_patient_identifier(cls, value: object) -> str | None:
        """Keep response IDs transport-safe while accepting UUID service output."""

        if value is None:
            return None
        return str(value)


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
        redis = get_async_redis_client()
        rate_key = f"nfc_resolve_rate:{provider.actor_uid}"
        current, retry_after = await atomic_fixed_window(redis, rate_key, 60)
        if current > 30:
            raise HTTPException(
                status_code=429,
                detail={
                    "error_code": "NFC_RATE_LIMITED",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(max(1, retry_after))},
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "NFC_SECURITY_CONTROL_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc

    resolver = CardResolutionService(db)
    redirect_service = CardRedirectService(db)

    try:
        patient_id = await resolver.resolve_card(payload.card_uid)

        # Tombstone redirect check (Section 9)
        redirect_result = await redirect_service.resolve_card_with_redirect(
            payload.card_uid
        )

        if redirect_result.get("is_redirected"):
            return NFCResolveResponse(
                patient_id=patient_id,
                canonical_patient_id=redirect_result.get("canonical_patient_uuid"),
                is_redirected=True,
            )

    except HTTPException:
        raise
    except TombstoneIntegrityError as exc:
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.NFC),
            actor_uid=provider.actor_uid,
            event_type="TOMBSTONE_INTEGRITY_VIOLATION",
            target_id=payload.card_uid,
            status="FAILED",
            metadata={"reason": "TOMBSTONE_INTEGRITY_VIOLATION"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "TOMBSTONE_INTEGRITY_VIOLATION"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NFC resolution service is temporarily unavailable.",
        ) from exc

    # Audit every NFC resolution attempt
    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.NFC),
        actor_uid=provider.actor_uid,
        event_type="NFC_CARD_RESOLVED",
        target_id=str(patient_id),
        status="SUCCESS",
        metadata={
            "card_uid": payload.card_uid[:8] + "...",  # partial for privacy
            "is_redirected": redirect_result.get("is_redirected", False),
        },
    )

    return NFCResolveResponse(patient_id=patient_id, is_redirected=False)
