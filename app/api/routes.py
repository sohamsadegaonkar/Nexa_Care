"""API router for Nexa Care endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.redis import issue_token
from app.models.schemas import (
    ClinicalRecordSchema,
    PIIVaultSchema,
    RegisterResponse,
    UnifiedPatientPayload,
)

router = APIRouter()


class ConsentRequest(BaseModel):
    masked_internal_id: UUID
    duration_seconds: int = Field(default=1800, ge=1, le=60 * 60 * 24)


@router.post("/register", response_model=RegisterResponse, tags=["sharding"])
async def register_patient(payload: UnifiedPatientPayload) -> RegisterResponse:
    masked_internal_id = uuid4()
    pii_vault = PIIVaultSchema(
        masked_internal_id=masked_internal_id,
        patient_name=payload.patient_name,
        phone=payload.phone,
        aadhaar_abha_id=payload.aadhaar_abha_id,
    )
    clinical_record = ClinicalRecordSchema(
        masked_internal_id=masked_internal_id,
        diagnoses=payload.diagnoses,
        lab_results=payload.lab_results,
        prescriptions=payload.prescriptions,
    )
    return RegisterResponse(pii_vault=pii_vault, clinical_record=clinical_record)


@router.post("/request-consent", tags=["consent"])
async def request_consent(payload: ConsentRequest) -> dict:
    consent_token = issue_token(
        masked_internal_id=str(payload.masked_internal_id),
        ttl_seconds=payload.duration_seconds,
    )
    return {"consent_token": consent_token, "expires_in": payload.duration_seconds}


@router.get("/view-record", tags=["consent"])
async def view_record(
    redis_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
) -> dict:
    if not redis_consent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing redis consent token",
        )
    return {
        "message": "consent verified",
        "clinical_record": {
            "diagnoses": ["mock-diagnosis"],
            "lab_results": ["mock-result"],
            "prescriptions": ["mock-prescription"],
        },
    }
