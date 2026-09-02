"""Provider-facing exact patient discovery with no pre-consent identity disclosure."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_clinical_capability
from app.security.provider_capabilities import ClinicalCapability
from app.core.redis import get_async_redis_client
from app.core.rate_limiter import atomic_fixed_window
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.patient_discovery_service import (
    DiscoveryNoMatch,
    DiscoveryUnavailable,
    PatientDiscoveryService,
)

router = APIRouter(prefix="/api/v2/patient-discovery", tags=["patient-discovery"])
_LIMIT = 12  # provisional engineering limit; bounded by provider/hospital/type.


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identifier_type: Literal["NEXA_PUBLIC_ID"]
    value: str = Field(min_length=3, max_length=32)


class DiscoveryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    discovery_handle: str
    expires_at: datetime


async def _audit(
    provider: ProviderContext, event: str, result: str, *, redirected: bool = False
) -> None:
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=provider.actor_uid,
        event_type=event,
        target_id="PATIENT_DISCOVERY",
        status=result,
        metadata={
            "identifier_type": "NEXA_PUBLIC_ID",
            "hospital_id": str(provider.hospital_id),
            "result": result,
            "redirected": redirected,
        },
    )


@router.post("", response_model=DiscoveryResponse)
async def discover_patient(
    payload: DiscoveryRequest,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.PATIENT_DISCOVER)
    ),
    db: AsyncSession = Depends(get_db_session),
) -> DiscoveryResponse:
    redis = get_async_redis_client()
    key = f"patient_discovery:{provider.actor_uid}:{provider.hospital_id}:{payload.identifier_type}"
    try:
        count, retry_after = await atomic_fixed_window(redis, key, 60)
        if count > _LIMIT:
            await _audit(provider, "PATIENT_DISCOVERY_RATE_LIMITED", "RATE_LIMITED")
            raise HTTPException(
                status_code=429,
                detail={
                    "error_code": "DISCOVERY_RATE_LIMITED",
                    "retry_after_seconds": retry_after,
                },
            )
        await _audit(provider, "PATIENT_DISCOVERY_ATTEMPTED", "ATTEMPTED")
        service = PatientDiscoveryService(db, redis)
        patient, redirected = await service.resolve_public_id(payload.value)
        handle = await service.issue_handle(
            patient=patient,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
            identifier_type=payload.identifier_type,
        )
        try:
            await _audit(
                provider,
                "PATIENT_DISCOVERY_SUCCEEDED",
                "SUCCESS",
                redirected=redirected,
            )
        except Exception as exc:
            # The handle was staged but must never become usable without its
            # mandatory success audit.  Its raw value has not left this process.
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
            # The raw handle was never disclosed and remains PENDING_AUDIT,
            # therefore it cannot be consumed even if hygiene deletion fails.
            try:
                await service.revoke_handle(raw_handle=handle.value)
            except DiscoveryUnavailable:
                pass
            raise HTTPException(
                status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
            )
        return DiscoveryResponse(
            discovery_handle=handle.value, expires_at=handle.expires_at
        )
    except HTTPException:
        raise
    except DiscoveryNoMatch:
        try:
            await _audit(provider, "PATIENT_DISCOVERY_NO_MATCH", "NO_MATCH")
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
            ) from exc
        raise HTTPException(
            status_code=404, detail={"error_code": "DISCOVERY_NO_MATCH"}
        )
    except (DiscoveryUnavailable, ValueError) as exc:
        await _audit(provider, "PATIENT_DISCOVERY_UNAVAILABLE", "UNAVAILABLE")
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
        ) from exc
    except Exception as exc:
        try:
            await _audit(provider, "PATIENT_DISCOVERY_UNAVAILABLE", "UNAVAILABLE")
        except Exception:
            pass
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
        ) from exc
