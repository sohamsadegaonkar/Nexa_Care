"""
API routes for Consent Assurance (Push + Biometric)
Wires the AssuranceService into the backend
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from uuid import UUID

from app.core.dependencies import get_provider_context
from app.core.redis import get_redis_client
from app.models.provider_context import ProviderContext
from app.services.assurance_service import AssuranceService
from app.observability.audit_ledger import append_audit_log
from app.observability.security_metrics import ASSURANCE_REQUESTS, BREAK_GLASS_REQUESTS

router = APIRouter(prefix="/api/v2/assurance", tags=["assurance"])
service = AssuranceService()


class PushApprovalRequest(BaseModel):
    patient_uuid: UUID
    clinician_name: str
    hospital_name: str
    purpose: str

    def __post_init__(self):
        self.clinician_name = self.clinician_name.strip()
        self.hospital_name = self.hospital_name.strip()
        self.purpose = self.purpose.strip()

        if not self.clinician_name:
            raise ValueError("clinician_name cannot be empty")
        if not self.hospital_name:
            raise ValueError("hospital_name cannot be empty")
        if not self.purpose:
            raise ValueError("purpose cannot be empty")


class PushApprovalResponse(BaseModel):
    approved: bool
    timeout: bool


class BiometricVerifyRequest(BaseModel):
    patient_uuid: UUID
    biometric_token: str

    def __post_init__(self):
        if not self.biometric_token.strip():
            raise ValueError("biometric_token cannot be empty")


@router.post("/push/request", response_model=PushApprovalResponse)
async def request_push_approval(
    payload: PushApprovalRequest,
    provider: ProviderContext = Depends(get_provider_context)
):
    """Trigger push notification for patient approval"""

    # Rate limiting: 5 push requests per provider per minute
    try:
        redis = get_redis_client()
        rate_key = f"assurance_push_rate:{provider.provider_id}"
        current = await redis.incr(rate_key)
        if current == 1:
            await redis.expire(rate_key, 60)
        if current > 5:
            raise HTTPException(status_code=429, detail="Too many push requests")
    except Exception:
        pass

    result = await service.request_push_approval(
        patient_uuid=payload.patient_uuid,
        clinician_name=payload.clinician_name,
        hospital_name=payload.hospital_name,
        purpose=payload.purpose,
    )

    await append_audit_log(
        actor_uid=provider.provider_id,
        event_type="PUSH_APPROVAL_REQUESTED",
        target_id=str(payload.patient_uuid),
        status="SUCCESS",
        metadata={
            "purpose": payload.purpose,
            "hospital": payload.hospital_name,
            "clinician": payload.clinician_name
        }
    )

    ASSURANCE_REQUESTS.labels(type="push", status="success").inc()

    return PushApprovalResponse(approved=result.approved, timeout=result.timeout)


@router.post("/biometric/verify")
async def verify_biometric(
    payload: BiometricVerifyRequest,
    provider: ProviderContext = Depends(get_provider_context)
):
    """Verify biometric confirmation from mobile app"""

    # Rate limiting: 5 biometric attempts per provider per minute
    try:
        redis = get_redis_client()
        rate_key = f"assurance_biometric_rate:{provider.provider_id}"
        current = await redis.incr(rate_key)
        if current == 1:
            await redis.expire(rate_key, 60)
        if current > 5:
            raise HTTPException(status_code=429, detail="Too many biometric attempts")
    except Exception:
        pass

    success = await service.verify_biometric(
        patient_uuid=payload.patient_uuid,
        biometric_token=payload.biometric_token,
    )

    await append_audit_log(
        actor_uid=provider.provider_id,
        event_type="BIOMETRIC_VERIFICATION_ATTEMPTED",
        target_id=str(payload.patient_uuid),
        status="SUCCESS" if success else "FAILED",
        metadata={"verified": success}
    )

    if not success:
        raise HTTPException(status_code=401, detail="Biometric verification failed")
    return {"verified": True}
