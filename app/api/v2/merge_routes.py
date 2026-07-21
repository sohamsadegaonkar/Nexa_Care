"""
Patient Merge (Alias & Tombstone) Workflow
Implements Section 9 of the Nexa Care v1.0 Architecture
"""

from __future__ import annotations

import inspect
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.core.redis import get_async_redis_client as get_redis_client
from app.models.provider_context import ProviderContext
from app.core.session_binding import provider_session_binding
from app.services.merge_service import PatientMergeService
from app.security.audit_context import AuditDomain, current_audit_context

router = APIRouter(prefix="/api/v2/patient", tags=["merge"])
_MERGE_CHALLENGE_PREFIX = "merge_challenge:"


async def _maybe_await(value):
    """Support sync redis-py and async fakes without changing route semantics."""
    if inspect.isawaitable(value):
        return await value
    return value


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
    request: Request,
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

    getdel = getattr(redis, "getdel", None)
    if getdel is not None:
        cached = await _maybe_await(getdel(key))
    else:
        cached = await _maybe_await(redis.get(key))
        if cached:
            await _maybe_await(redis.delete(key))

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

    if (
        challenge_data.get("provider_id") != str(provider.provider.provider_id)
        or challenge_data.get("hospital_id") != str(provider.hospital.hospital_id)
        or challenge_data.get("session_binding") != provider_session_binding(request)
        or challenge_data.get("operation") != "patient_merge"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "MERGE_CHALLENGE_BINDING_MISMATCH"},
        )

    from app.observability.audit_ledger import append_audit_log_or_503

    try:
        service = PatientMergeService(db)
        tombstone = await service.merge_patients(
            old_uuid=payload.old_patient_uuid,
            canonical_uuid=payload.canonical_patient_uuid,
            reason=payload.reason,
            evidence=payload.evidence,
            merged_by=provider.actor_uid,
        )

        resolved_canonical_uuid = (
            tombstone.canonical_patient_uuid
            if isinstance(getattr(tombstone, "canonical_patient_uuid", None), UUID)
            else payload.canonical_patient_uuid
        )

        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.MERGE),
            actor_uid=provider.actor_uid,
            event_type="MERGE_EXECUTED",
            target_id=str(payload.old_patient_uuid),
            status="SUCCESS",
            metadata={"canonical_patient_uuid": str(resolved_canonical_uuid)},
        )

        return MergeResponse(
            message="Patient merged successfully. Old record is now a tombstone.",
            tombstone_id=tombstone.tombstone_id,
            canonical_patient_uuid=resolved_canonical_uuid,
        )
    except ValueError as e:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.MERGE),
            actor_uid=provider.actor_uid,
            event_type="MERGE_REJECTED",
            target_id=str(payload.old_patient_uuid),
            status="FAILED",
            metadata={
                "canonical_patient_uuid": str(payload.canonical_patient_uuid),
                "reason": "MERGE_VALIDATION_FAILED",
            },
        )
        raise HTTPException(
            status_code=400, detail={"error_code": "MERGE_VALIDATION_FAILED"}
        ) from e
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Merge operation failed") from exc
