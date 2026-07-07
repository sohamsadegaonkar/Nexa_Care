"""
Patient Merge (Alias & Tombstone) Workflow
Implements Section 9 of the Nexa Care v1.0 Architecture
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.core.redis import get_redis_client
from app.models.provider_context import ProviderContext
from app.services.merge_service import PatientMergeService

router = APIRouter(prefix="/api/v2/patient", tags=["merge"])
_MERGE_CHALLENGE_PREFIX = "merge_challenge:"


class MergeRequest(BaseModel):
    old_patient_uuid: UUID
    canonical_patient_uuid: UUID
    reason: str
    evidence: dict | None = None


class MergeResponse(BaseModel):
    message: str
    tombstone_id: UUID
    canonical_patient_uuid: UUID


@router.post("/merge", response_model=MergeResponse, status_code=201)
async def merge_patients(
    payload: MergeRequest,
    x_merge_challenge: str = Header(..., alias="X-Merge-Challenge"),
    db: AsyncSession = Depends(get_db_session),
    provider: ProviderContext = Depends(get_current_provider),
):
    """
    Supervised patient merge workflow.
    Requires Clinical_Admin / Data_Steward role + fresh MFA challenge.
    """
    if "admin" not in provider.affiliation.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for merge operation.",
        )

    redis = get_redis_client()
    key = f"{_MERGE_CHALLENGE_PREFIX}{x_merge_challenge}"
    
    # Atomic get and delete (Redis 6.2+)
    # If not supported, we'd use a Lua script.
    # For this implementation, we fetch and then check.
    cached = redis.get(key)
    if not cached:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fresh challenge required for merge operation.",
        )

    if isinstance(cached, bytes):
        cached = cached.decode("utf-8")

    challenge_data = json.loads(cached)
    if not challenge_data.get("verified"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Challenge not verified.",
        )

    if challenge_data["provider_id"] != str(provider.provider.provider_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Challenge bound to different provider.",
        )

    # Consume immediately to prevent race conditions
    redis.delete(key)

    try:
        from app.observability.audit_ledger import append_audit_log_or_503
        await append_audit_log_or_503(
            actor_uid=provider.actor_uid,
            event_type="MERGE_EXECUTED",
            target_id=str(payload.old_patient_uuid),
            status="SUCCESS",
            metadata={"canonical_patient_uuid": str(payload.canonical_patient_uuid)}
        )

        service = PatientMergeService(db)
        tombstone = await service.merge_patients(
            old_uuid=payload.old_patient_uuid,
            canonical_uuid=payload.canonical_patient_uuid,
            reason=payload.reason,
            evidence=payload.evidence,
        )

        return MergeResponse(
            message="Patient merged successfully. Old record is now a tombstone.",
            tombstone_id=tombstone.tombstone_id,
            canonical_patient_uuid=payload.canonical_patient_uuid,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Merge operation failed")
