from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel

# Canonical consent-assurance levels, shared across the consent engine,
# the assurance service (push/biometric), and the frontend's ConsentAssurance
# type in packages/app/api/consent_v1.ts. Keep these two lists in sync.
ConsentAssurance = Literal[
    "standard",
    "push_approved",
    "biometric_confirmed",
    "bypassed_emergency",
    "standard_fallback_from_push",
]


class ConsentIssueRequest(BaseModel):
    patient_uuid: UUID
    hospital_id: str
    clinician_id: str
    purpose: str
    consent_assurance: ConsentAssurance = "standard"


class ConsentResponse(BaseModel):
    consent_id: UUID
    consent_token: str
    patient_uuid: UUID
    purpose: str
    consent_assurance: ConsentAssurance
    granted_at: datetime
    expires_at: datetime


class BreakGlassRequest(BaseModel):
    patient_uuid: UUID
    hospital_id: str
    clinician_id: str
    reason: str
    justification: str