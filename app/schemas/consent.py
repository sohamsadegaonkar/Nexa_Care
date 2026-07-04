from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel


class ConsentIssueRequest(BaseModel):
    patient_uuid: UUID
    hospital_id: str
    clinician_id: str
    purpose: str
    consent_assurance: Literal[
        "standard",
        "push_approved",
        "biometric_confirmed",
        "bypassed_emergency",
        "standard_fallback_from_push"
    ] = "standard"


class ConsentResponse(BaseModel):
    consent_id: UUID
    consent_token: str
    patient_uuid: UUID
    purpose: str
    consent_assurance: str
    granted_at: datetime
    expires_at: datetime


class BreakGlassRequest(BaseModel):
    patient_uuid: UUID
    hospital_id: str
    clinician_id: str
    reason: str
    justification: str