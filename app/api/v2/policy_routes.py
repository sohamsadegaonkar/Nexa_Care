from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from uuid import UUID
import os
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_provider_context
from app.core.redis import get_redis_client
from app.models.provider_context import ProviderContext
from app.services.policy_service import PolicyService
from app.observability.audit_ledger import append_audit_log
from app.observability.security_metrics import POLICY_UPDATES

router = APIRouter(prefix="/api/v2/patient", tags=["policy"])

class PolicyUpdateRequest(BaseModel):
    consent_assurance_policy: str

ALLOWED_POLICY_ROLES = {"clinician", "admin"}

# Server-side protection for dev-only policy simulator
ALLOW_DEV_POLICY_UPDATES = os.getenv("ALLOW_DEV_POLICY_UPDATES", "false").lower() == "true"

@router.get("/{patient_uuid}/policy")
async def get_patient_policy(
    patient_uuid: UUID,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db)
):
    service = PolicyService(db)
    policy = await service.get_policy(patient_uuid)
    return {"patient_uuid": str(patient_uuid), "consent_assurance_policy": policy}

@router.put("/{patient_uuid}/policy")
async def update_patient_policy(
    patient_uuid: UUID,
    payload: PolicyUpdateRequest,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db)
):
    # Authorization: Only certain roles can change patient consent policy
    if provider.role not in ALLOWED_POLICY_ROLES:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to change patient consent policies."
        )

    # Extra protection: Block policy updates unless explicitly allowed in dev
    if not ALLOW_DEV_POLICY_UPDATES:
        raise HTTPException(
            status_code=403,
            detail="Policy updates via this endpoint are disabled."
        )

    # Rate limiting: Max 10 policy updates per provider per minute
    try:
        redis = get_redis_client()
        rate_key = f"policy_update_rate:{provider.provider_id}"
        current = await redis.incr(rate_key)

        if current == 1:
            await redis.expire(rate_key, 60)

        if current > 10:
            raise HTTPException(
                status_code=429,
                detail="Too many policy updates. Please try again later."
            )
    except Exception:
        # Fail open if Redis is unavailable
        pass

    # Get current policy for audit
    service = PolicyService(db)
    old_policy = await service.get_policy(patient_uuid)

    # Update policy
    updated = await service.set_policy(patient_uuid, payload.consent_assurance_policy)

    # Detect if this came from the dev Policy Simulator
    is_simulator = request.headers.get("x-dev-simulator") == "true"
    current_env = os.getenv("ENV", "production")

    # Extra safety signal: Log warning if simulator is used outside dev
    if is_simulator and current_env not in ("development", "staging"):
        import logging
        logging.getLogger("nexa_security").warning(
            f"Policy Simulator used in non-dev environment by provider {provider.provider_id}"
        )

    # Audit log the change
    await append_audit_log(
        actor_uid=provider.provider_id,
        event_type="PATIENT_POLICY_CHANGED",
        target_id=str(patient_uuid),
        status="SUCCESS",
        metadata={
            "old_policy": old_policy,
            "new_policy": updated,
            "changed_by_role": provider.role,
            "via_simulator": is_simulator,
            "environment": current_env
        }
    )

    # Prometheus metric
    POLICY_UPDATES.labels(
        status="success",
        role=provider.role,
        via_simulator=str(is_simulator).lower()
    ).inc()

    return {"patient_uuid": str(patient_uuid), "consent_assurance_policy": updated}