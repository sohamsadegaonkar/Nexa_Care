"""
Patient Merge (Alias & Tombstone) Workflow
Implements Section 9 of the Nexa Care v1.0 Architecture
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.services.merge_service import PatientMergeService

router = APIRouter(prefix="/api/v2/patient", tags=["merge"])


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
    db: AsyncSession = Depends(get_db),
):
    """
    Supervised patient merge workflow.
    Requires Clinical_Admin / Data_Steward role + MFA (enforced at auth layer).
    """
    try:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail="Merge operation failed")