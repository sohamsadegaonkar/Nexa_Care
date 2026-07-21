from app.security.audit_context import AuditDomain, current_audit_context
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.consent_gate import validate_consent_for_patient
from app.core.dependencies import get_provider_context
from app.core.redis import get_async_redis_client
from app.core.rate_limiter import atomic_fixed_window
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.observability.security_metrics import POLICY_UPDATES
from app.services.policy_service import (
    PolicyIdempotencyKeyReused,
    PolicyService,
    PolicyValidationError,
    PolicyVersionConflict,
)

logger = logging.getLogger("nexa_security")

router = APIRouter(prefix="/api/v2/patient", tags=["policy"])

class PolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent_assurance_policy: Literal["standard", "push_approved", "push_biometric", "biometric_confirmed"]
    idempotency_key: str
    expected_version: int = Field(ge=0)

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
            audit_context=current_audit_context(AuditDomain.POLICY),
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
    policy_row = await service.get_policy_row(patient_uuid)
    policy = policy_row.consent_assurance_policy if policy_row else "standard"
    version = policy_row.version if policy_row else 0
    await append_audit_log(
        audit_context=current_audit_context(AuditDomain.POLICY),
        actor_uid=provider.actor_uid,
        event_type="PATIENT_POLICY_READ_SUCCESS",
        target_id=str(patient_uuid),
        status="SUCCESS",
        metadata={"hospital_id": str(provider.hospital_id)},
    )
    return {"patient_uuid": str(patient_uuid), "consent_assurance_policy": policy, "version": version}

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
            audit_context=current_audit_context(AuditDomain.POLICY),
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

    service = PolicyService(db)
    changed_by_role = next(iter(roles & ALLOWED_POLICY_ROLES))

    # DEFECT 6: one transaction -- CAS-update the policy, insert the
    # audit-outbox event, commit. No separate append_audit_log() call here;
    # the outbox processor is the only thing that appends this event to the
    # immutable ledger, so it is never written twice.
    try:
        result = await service.set_policy_atomic(
            patient_uuid,
            payload.consent_assurance_policy,
            expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key,
            actor_id=provider.actor_uid,
            tenant_id=str(provider.hospital_id),
        )
    except PolicyValidationError as err:
        raise HTTPException(
            status_code=422, detail={"error_code": "POLICY_REQUEST_INVALID", "message": str(err)}
        ) from err
    except PolicyVersionConflict as err:
        raise HTTPException(
            status_code=409, detail={"error_code": "POLICY_VERSION_CONFLICT", "message": str(err)}
        ) from err
    except PolicyIdempotencyKeyReused as err:
        raise HTTPException(
            status_code=409, detail={"error_code": "IDEMPOTENCY_KEY_REUSED", "message": str(err)}
        ) from err

    updated = result.consent_assurance_policy

    # Prometheus metric
    POLICY_UPDATES.labels(
        status="success",
        role=changed_by_role,
        via_simulator=str(is_simulator).lower(),
    ).inc()

    return {
        "patient_uuid": str(patient_uuid),
        "consent_assurance_policy": updated,
        "version": result.version,
        "idempotent_replay": result.idempotent_replay,
    }
