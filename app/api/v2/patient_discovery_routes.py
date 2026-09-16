"""Provider-facing exact patient discovery with no pre-consent identity disclosure."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_clinical_capability
from app.core.redis import get_async_redis_client
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.provider_capabilities import ClinicalCapability
from app.services.patient_discovery_abuse_control import (
    DiscoveryAbuseControlUnavailable,
    DiscoveryRateLimited,
    enforce_patient_discovery_budget,
)
from app.services.patient_discovery_high_risk_gate import (
    PatientDiscoveryHighRiskGateError,
    require_recent_mfa_for_phone_discovery,
)
from app.services.patient_discovery_input import normalize_qr_public_id
from app.services.patient_discovery_service import (
    DiscoveryNoMatch,
    DiscoveryUnavailable,
    PatientDiscoveryService,
)
from app.services.patient_search_identifier_service import (
    PatientSearchIdentifierNoMatch,
    PatientSearchIdentifierUnavailable,
    resolve_verified_phone_patient,
)

router = APIRouter(prefix="/api/v2/patient-discovery", tags=["patient-discovery"])
DiscoveryIdentifierType = Literal["NEXA_PUBLIC_ID", "PHONE", "QR_PUBLIC_ID"]


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    identifier_type: DiscoveryIdentifierType
    value: str = Field(min_length=3, max_length=128)


class DiscoveryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    discovery_handle: str
    expires_at: datetime


async def _audit(
    provider: ProviderContext,
    identifier_type: DiscoveryIdentifierType,
    event: str,
    result: str,
    *,
    redirected: bool = False,
) -> None:
    """Write value-free discovery evidence; searched identifiers are never logged."""
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=provider.actor_uid,
        event_type=event,
        target_id="PATIENT_DISCOVERY",
        status=result,
        metadata={
            "identifier_type": identifier_type,
            "hospital_id": str(provider.hospital_id),
            "result": result,
            "redirected": redirected,
        },
    )


async def _resolve_exact(
    *,
    payload: DiscoveryRequest,
    service: PatientDiscoveryService,
    db: AsyncSession,
):
    if payload.identifier_type == "NEXA_PUBLIC_ID":
        return await service.resolve_public_id(payload.value)
    if payload.identifier_type == "QR_PUBLIC_ID":
        return await service.resolve_public_id(normalize_qr_public_id(payload.value))
    if payload.identifier_type == "PHONE":
        return await resolve_verified_phone_patient(db, phone=payload.value)
    raise DiscoveryUnavailable()


@router.post("", response_model=DiscoveryResponse)
async def discover_patient(
    payload: DiscoveryRequest,
    request: Request,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.PATIENT_DISCOVER)
    ),
    db: AsyncSession = Depends(get_db_session),
) -> DiscoveryResponse:
    """Resolve one exact identifier to an audited, opaque, one-use capability.

    PHONE is intentionally higher assurance: the patient must have opted into a
    verified phone index and the provider must present recent MFA on the exact
    live provider session. All modes retain the same minimum-disclosure handle.
    """

    redis = get_async_redis_client()
    identifier_type = payload.identifier_type

    try:
        await enforce_patient_discovery_budget(
            redis,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            identifier_type=identifier_type,
        )
    except DiscoveryRateLimited as exc:
        try:
            await _audit(
                provider,
                identifier_type,
                "PATIENT_DISCOVERY_RATE_LIMITED",
                "RATE_LIMITED",
            )
        except Exception as audit_exc:
            raise HTTPException(
                status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
            ) from audit_exc
        raise HTTPException(
            status_code=429,
            detail={
                "error_code": "DISCOVERY_RATE_LIMITED",
                "retry_after_seconds": exc.retry_after_seconds,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None
    except DiscoveryAbuseControlUnavailable as exc:
        try:
            await _audit(
                provider,
                identifier_type,
                "PATIENT_DISCOVERY_UNAVAILABLE",
                "UNAVAILABLE",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail={"error_code": "DISCOVERY_SECURITY_CONTROL_UNAVAILABLE"},
        ) from exc

    if identifier_type == "PHONE":
        try:
            await require_recent_mfa_for_phone_discovery(
                request,
                provider=provider,
            )
        except PatientDiscoveryHighRiskGateError as exc:
            try:
                await _audit(
                    provider,
                    identifier_type,
                    "PATIENT_DISCOVERY_AUTHORITY_REJECTED",
                    "REJECTED",
                )
            except Exception as audit_exc:
                raise HTTPException(
                    status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
                ) from audit_exc
            status_code = (
                403
                if exc.code == "DISCOVERY_RECENT_MFA_REQUIRED"
                else 401
            )
            raise HTTPException(
                status_code=status_code,
                detail={"error_code": exc.code},
            ) from None

    try:
        await _audit(
            provider,
            identifier_type,
            "PATIENT_DISCOVERY_ATTEMPTED",
            "ATTEMPTED",
        )
        service = PatientDiscoveryService(db, redis)
        patient, redirected = await _resolve_exact(
            payload=payload,
            service=service,
            db=db,
        )
        handle = await service.issue_handle(
            patient=patient,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
            identifier_type=identifier_type,
        )
        try:
            await _audit(
                provider,
                identifier_type,
                "PATIENT_DISCOVERY_SUCCEEDED",
                "SUCCESS",
                redirected=redirected,
            )
        except Exception as exc:
            # The handle was staged but must never become usable without its
            # mandatory success audit. Its raw value has not left this process.
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
            # A PENDING_AUDIT handle is inert even if hygiene deletion fails.
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
    except (DiscoveryNoMatch, PatientSearchIdentifierNoMatch):
        try:
            await _audit(
                provider,
                identifier_type,
                "PATIENT_DISCOVERY_NO_MATCH",
                "NO_MATCH",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
            ) from exc
        # PHONE absent, opted-out, or no-longer-current all collapse to the same
        # public result. The provider never receives account/index diagnostics.
        raise HTTPException(
            status_code=404, detail={"error_code": "DISCOVERY_NO_MATCH"}
        ) from None
    except (DiscoveryUnavailable, PatientSearchIdentifierUnavailable) as exc:
        try:
            await _audit(
                provider,
                identifier_type,
                "PATIENT_DISCOVERY_UNAVAILABLE",
                "UNAVAILABLE",
            )
        except Exception as audit_exc:
            raise HTTPException(
                status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
            ) from audit_exc
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
        ) from exc
    except ValueError:
        # Malformed public-ID and QR inputs intentionally look like no matches,
        # rather than exposing parser detail for a discovery oracle.
        try:
            await _audit(
                provider,
                identifier_type,
                "PATIENT_DISCOVERY_NO_MATCH",
                "NO_MATCH",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
            ) from exc
        raise HTTPException(
            status_code=404, detail={"error_code": "DISCOVERY_NO_MATCH"}
        ) from None
    except Exception as exc:
        try:
            await _audit(
                provider,
                identifier_type,
                "PATIENT_DISCOVERY_UNAVAILABLE",
                "UNAVAILABLE",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=503, detail={"error_code": "DISCOVERY_UNAVAILABLE"}
        ) from exc
