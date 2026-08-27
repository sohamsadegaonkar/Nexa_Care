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
from app.core.dependencies import require_role
from app.core.redis import get_async_redis_client
from app.core.rate_limiter import atomic_fixed_window
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.services.card_resolution_service import CardResolutionService
from app.services.card_redirect_service import TombstoneIntegrityError
from app.services.patient_discovery_service import (
    DiscoveryUnavailable,
    PatientDiscoveryService,
)

router = APIRouter(prefix="/api/v2/nfc", tags=["nfc"])


async def _audit_terminal(provider: ProviderContext, *, status_value: str) -> None:
    """Record an NFC terminal outcome without card or patient identifiers."""
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.NFC),
        actor_uid=provider.actor_uid,
        event_type="NFC_CARD_RESOLUTION_DENIED",
        target_id="NFC_DISCOVERY",
        status=status_value,
        metadata={"identifier_type": "NFC"},
    )


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

    discovery_handle: str
    expires_at: str


@router.post("/resolve", response_model=NFCResolveResponse)
async def resolve_nfc_card(
    payload: NFCResolveRequest,
    provider: ProviderContext = Depends(require_role("clinician")),
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
        await _audit_terminal(provider, status_value="DENIED")
        raise
    except Exception as exc:
        try:
            await _audit_terminal(provider, status_value="UNAVAILABLE")
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "NFC_SECURITY_CONTROL_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc

    service = PatientDiscoveryService(db, redis)
    resolver = CardResolutionService(db)
    try:
        patient_id = await resolver.resolve_card(payload.card_uid)
        patient, redirected = await service.resolve_patient_id(patient_id)

    except HTTPException:
        await _audit_terminal(provider, status_value="DENIED")
        raise
    except TombstoneIntegrityError as exc:
        await _audit_terminal(provider, status_value="UNAVAILABLE")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "NFC_SECURITY_CONTROL_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc
    except Exception as exc:
        try:
            await _audit_terminal(provider, status_value="UNAVAILABLE")
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "NFC_SECURITY_CONTROL_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc

    try:
        handle = await service.issue_handle(
            patient=patient,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
            identifier_type="NFC",
        )
    except DiscoveryUnavailable as exc:
        try:
            await _audit_terminal(provider, status_value="UNAVAILABLE")
        except Exception:
            pass
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
        ) from exc
    try:
        # The opaque handle is staged before the audit and disclosed only after
        # the ledger accepts the terminal success record.
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.NFC),
            actor_uid=provider.actor_uid,
            event_type="NFC_CARD_RESOLVED",
            target_id="NFC_DISCOVERY",
            status="SUCCESS",
            metadata={"identifier_type": "NFC", "is_redirected": redirected},
        )
    except Exception as exc:
        try:
            await service.revoke_handle(raw_handle=handle.value)
        except DiscoveryUnavailable:
            pass
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
        ) from exc
    try:
        activated = await service.activate_handle(raw_handle=handle.value)
    except DiscoveryUnavailable:
        activated = False
    if not activated:
        # The staged value is inert unless activation succeeds. Deletion here
        # is hygiene only and never establishes the security guarantee.
        try:
            await service.revoke_handle(raw_handle=handle.value)
        except DiscoveryUnavailable:
            pass
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
        )
    return NFCResolveResponse(
        discovery_handle=handle.value, expires_at=handle.expires_at.isoformat()
    )
