from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base

class PatientPolicy(Base):
    __tablename__ = "patient_policies"

    patient_uuid = Column(UUID(as_uuid=True), ForeignKey("patients.patient_uuid"), primary_key=True)
    consent_assurance_policy = Column(String, nullable=False, default="standard")
    updated_at = Column(String)  # simplified for now