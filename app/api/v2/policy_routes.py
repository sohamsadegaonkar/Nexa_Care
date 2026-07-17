import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.consent_gate import validate_consent_for_patient
from app.core.dependencies import get_provider_context
from app.core.redis import get_async_redis_client
from app.core.rate_limiter import atomic_fixed_window
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.observability.security_metrics import POLICY_UPDATES
from app.services.policy_service import PolicyService

logger = logging.getLogger("nexa_security")

router = APIRouter(prefix="/api/v2/patient", tags=["policy"])

class PolicyUpdateRequest(BaseModel):
    consent_assurance_policy: str

ALLOWED_POLICY_ROLES = {"clinician", "admin"}

# Non-dev/staging environments must never honor a simulator-tagged request,
# regardless of what header the caller sends. This does NOT gate real
# (non-simulator) policy updates from PolicyScreen/RoleNavigator — those are
# controlled solely by ALLOWED_POLICY_ROLES + rate limiting below.
@router.get("/{patient_uuid}/policy")
async def get_patient_policy(
    patient_uuid: UUID,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
):
    roles = set(provider.affiliation.roles or [])
    if not roles & ALLOWED_POLICY_ROLES:
        await append_audit_log(
            actor_uid=provider.actor_uid,
            event_type="PATIENT_POLICY_READ_DENIED",
            target_id=str(patient_uuid),
            status="FORBIDDEN_ROLE",
        )
        raise HTTPException(status_code=403, detail="Patient policy access is not authorized.")
    await validate_consent_for_patient(
        patient_id=str(patient_uuid),
        purpose="policy_read",
        provider=provider,
        x_consent_token=x_consent_token,
    )
    service = PolicyService(db)
    policy = await service.get_policy(patient_uuid)
    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="PATIENT_POLICY_READ_SUCCESS",
        target_id=str(patient_uuid),
        status="SUCCESS",
        metadata={"hospital_id": str(provider.hospital_id)},
    )
    return {"patient_uuid": str(patient_uuid), "consent_assurance_policy": policy}

@router.put("/{patient_uuid}/policy")
async def update_patient_policy(
    patient_uuid: UUID,
    payload: PolicyUpdateRequest,
    request: Request,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
):
    roles = set(provider.affiliation.roles or [])
    provider_id = provider.provider.provider_id

    # Detect if this came from the dev Policy Simulator
    is_simulator = request.headers.get("x-dev-simulator") == "true"

    # Simulator-tagged requests are only ever honored in dev/staging, no
    # matter who sends them or what role they hold. This does not affect
    # real (non-simulator) updates from PolicyScreen/RoleNavigator.
    from app.core.config import get_runtime_environment

    runtime = get_runtime_environment()
    if is_simulator and not runtime.allows_simulator:
        logger.warning(
            "Policy Simulator header used in non-dev environment '%s' by provider %s",
            runtime.value,
            provider_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Policy updates via the dev simulator are disabled in this environment.",
        )

    # Authorization: Only certain roles can change patient consent policy
    if not roles & ALLOWED_POLICY_ROLES:
        await append_audit_log(
            actor_uid=provider.actor_uid,
            event_type="PATIENT_POLICY_UPDATE_DENIED",
            target_id=str(patient_uuid),
            status="FORBIDDEN_ROLE",
        )
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to change patient consent policies.",
        )

    await validate_consent_for_patient(
        patient_id=str(patient_uuid),
        purpose="policy_update",
        provider=provider,
        x_consent_token=x_consent_token,
    )

    # Rate limiting: Max 10 policy updates per provider per minute
    try:
        redis = get_async_redis_client()
        rate_key = f"policy_update_rate:{provider_id}"
        current, retry_after = await atomic_fixed_window(redis, rate_key, 60)

        if current > 10:
            raise HTTPException(
                status_code=429,
                detail={"error_code": "POLICY_RATE_LIMITED", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(max(1, retry_after))},
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "POLICY_SECURITY_CONTROL_UNAVAILABLE", "retryable": True},
        ) from exc

    # Get current policy for audit
    service = PolicyService(db)
    old_policy = await service.get_policy(patient_uuid)

    # Update policy
    updated = await service.set_policy(patient_uuid, payload.consent_assurance_policy)

    changed_by_role = next(iter(roles & ALLOWED_POLICY_ROLES))

    # Audit log the change
    await append_audit_log(
        actor_uid=provider.actor_uid,
        event_type="PATIENT_POLICY_CHANGED",
        target_id=str(patient_uuid),
        status="SUCCESS",
        metadata={
            "old_policy": old_policy,
            "new_policy": updated,
            "changed_by_role": changed_by_role,
            "via_simulator": is_simulator,
            "environment": runtime.value,
        },
    )

    # Prometheus metric
    POLICY_UPDATES.labels(
        status="success",
        role=changed_by_role,
        via_simulator=str(is_simulator).lower(),
    ).inc()

    return {"patient_uuid": str(patient_uuid), "consent_assurance_policy": updated}
