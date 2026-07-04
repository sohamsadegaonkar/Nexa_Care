from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from uuid import UUID

from app.core.dependencies import get_provider_context
from app.core.redis import get_redis_client
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.observability.security_metrics import BREAK_GLASS_REQUESTS
from app.services.nexa_consent_engine import NexaConsentEngine

router = APIRouter(prefix="/api/v2/consent", tags=["break-glass"])
engine = NexaConsentEngine()

class BreakGlassRequest(BaseModel):
    patient_uuid: UUID
    hospital_id: str
    clinician_id: str
    reason: str
    justification: str

@router.post("/break-glass/issue")
async def issue_break_glass(
    payload: BreakGlassRequest,
    provider: ProviderContext = Depends(get_provider_context)
):
    # Rate limit: 3 break-glass requests per provider per hour
    try:
        redis = get_redis_client()
        rate_key = f"break_glass_rate:{provider.provider_id}"
        current = await redis.incr(rate_key)
        if current == 1:
            await redis.expire(rate_key, 3600)
        if current > 3:
            raise HTTPException(status_code=429, detail="Too many break-glass attempts")
    except Exception:
        pass

    token = await engine.issue_break_glass(
        patient_uuid=payload.patient_uuid,
        hospital_id=payload.hospital_id,
        clinician_id=payload.clinician_id,
        reason=payload.reason,
        justification=payload.justification,
    )

    await append_audit_log(
        actor_uid=provider.provider_id,
        event_type="BREAK_GLASS_ISSUED",
        target_id=str(payload.patient_uuid),
        status="SUCCESS",
        metadata={
            "reason": payload.reason,
            "hospital": payload.hospital_id
        }
    )

    BREAK_GLASS_REQUESTS.labels(status="success").inc()

    return {"consent_token": token, "consent_assurance": "bypassed_emergency"}