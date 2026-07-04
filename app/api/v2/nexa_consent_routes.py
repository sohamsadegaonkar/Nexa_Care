"""
Nexa Care v1.0 Consent Routes
Tap → Consent → Session flow endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.services.nexa_consent_engine import NexaConsentEngine
from app.schemas.consent import (
    ConsentIssueRequest,
    ConsentResponse,
    BreakGlassRequest,
)

router = APIRouter(prefix="/api/v2/consent", tags=["consent-v1"])
engine = NexaConsentEngine()


@router.post("/routine/issue", response_model=ConsentResponse, status_code=201)
async def issue_routine_consent(
    payload: ConsentIssueRequest,
    db: AsyncSession = Depends(get_db),
):
    """Issue routine consent token after card tap + assurance check"""
    try:
        token = await engine.issue_routine_consent(
            db,
            patient_uuid=payload.patient_uuid,
            hospital_id=payload.hospital_id,
            clinician_id=payload.clinician_id,
            purpose=payload.purpose,
            consent_assurance=payload.consent_assurance,
        )
        return ConsentResponse(
            consent_id=UUID(int=0),  # placeholder - in real impl use ledger id
            consent_token=token,
            patient_uuid=payload.patient_uuid,
            purpose=payload.purpose,
            consent_assurance=payload.consent_assurance,
            granted_at="now",
            expires_at="30m",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/break-glass/issue", response_model=ConsentResponse, status_code=201)
async def issue_break_glass(
    payload: BreakGlassRequest,
    db: AsyncSession = Depends(get_db),
):
    """Emergency break-glass access"""
    token = await engine.issue_break_glass(
        db,
        patient_uuid=payload.patient_uuid,
        hospital_id=payload.hospital_id,
        clinician_id=payload.clinician_id,
        reason=payload.reason,
        justification=payload.justification,
    )
    return ConsentResponse(
        consent_id=UUID(int=0),
        consent_token=token,
        patient_uuid=payload.patient_uuid,
        purpose="EMERGENCY",
        consent_assurance="bypassed_emergency",
        granted_at="now",
        expires_at="15m",
    )


@router.get("/validate")
async def validate_consent(
    consent_token: str,
    patient_uuid: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Validate consent token (used by terminals for revalidation)"""
    result = await engine.validate_consent(db, consent_token, patient_uuid)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid or expired consent")
    return result