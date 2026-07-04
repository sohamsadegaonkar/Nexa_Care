from fastapi import APIRouter, Depends
from typing import List
from pydantic import BaseModel
from uuid import UUID
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext

router = APIRouter(prefix="/api/v2/patient", tags=["transparency"])

class AccessLog(BaseModel):
    timestamp: str
    clinician_id: str
    hospital_id: str
    purpose: str
    consent_assurance: str
    action: str

@router.get("/{patient_uuid}/access-log", response_model=List[AccessLog])
async def get_patient_access_log(
    patient_uuid: UUID,
    provider: ProviderContext = Depends(get_provider_context)
):
    """
    Returns access history for a patient.
    In production, this would query the audit_ledger.
    """
    # Mock data for now (replace with real query later)
    return [
        AccessLog(
            timestamp="2026-07-04 09:12",
            clinician_id="DR-042",
            hospital_id="HOSP-001",
            purpose="ROUTINE_CHECKUP",
            consent_assurance="standard",
            action="record_viewed"
        ),
        AccessLog(
            timestamp="2026-07-03 14:55",
            clinician_id="DR-089",
            hospital_id="HOSP-002",
            purpose="EMERGENCY",
            consent_assurance="bypassed_emergency",
            action="break_glass_access"
        ),
    ]