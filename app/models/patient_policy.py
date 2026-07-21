from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base

class PatientPolicy(Base):
    __tablename__ = "patient_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "patient_uuid",
            name="uq_patient_policies_tenant_patient",
        ),
    )

    patient_uuid = Column(
        UUID(as_uuid=True),
        ForeignKey("patients.patient_uuid", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id = Column(String(128), nullable=True)
    consent_assurance_policy = Column(
        String,
        nullable=False,
        default="standard",
        server_default=text("'standard'"),
    )
    updated_at = Column(String)  # simplified for now
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    last_idempotency_key = Column(String(128), nullable=True)
