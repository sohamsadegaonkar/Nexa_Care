from datetime import date, datetime
from typing import Optional, Literal
from uuid import UUID
from pydantic import BaseModel


class PatientCreate(BaseModel):
    full_name: str
    date_of_birth: date
    gender: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    abha_id: Optional[str] = None
    consent_assurance_policy: Literal["STANDARD", "PUSH_APPROVAL", "BIOMETRIC"] = "STANDARD"


class PatientResponse(BaseModel):
    patient_uuid: UUID
    full_name: Optional[str]
    date_of_birth: Optional[date]
    consent_assurance_policy: str
    created_at: datetime


class PatientExternalId(BaseModel):
    id_type: str
    id_value: str
    verified: bool = False


class CardRegisterRequest(BaseModel):
    patient_uuid: UUID
    card_id: str


class CardResponse(BaseModel):
    card_id: str
    patient_uuid: UUID
    status: str
    issued_at: datetime