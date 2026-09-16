"""Durable lifecycle record for bounded provider clinical-access sessions.

The raw bearer capability is never persisted. PostgreSQL stores only its
SHA-256 digest together with the server-owned authority bindings required to
explain, revoke, and qualify a clinical access session.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ClinicalAccessSessionRecord(Base):
    """One durable bounded clinical-access authority derived from patient consent."""

    __tablename__ = "clinical_access_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.patient_uuid", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=False,
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_registry.id", ondelete="RESTRICT"),
        nullable=False,
    )
    consent_request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_operations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    provider_session_binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    encounter_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_clinical_access_session_token_hash",
        ),
        CheckConstraint(
            "provider_session_binding_hash ~ '^[0-9a-f]{64}$'",
            name="ck_clinical_access_session_binding_hash",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_clinical_access_session_positive_lifetime",
        ),
        CheckConstraint(
            "scope IN ('clinical','full')",
            name="ck_clinical_access_session_scope",
        ),
        CheckConstraint(
            "policy_version = 'clinical-access-v1'",
            name="ck_clinical_access_session_policy_v1",
        ),
        CheckConstraint(
            "jsonb_typeof(allowed_operations) = 'array' "
            "AND allowed_operations = '[\"READ_CLINICAL_HISTORY\"]'::jsonb",
            name="ck_clinical_access_session_ops_v1",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','REVOKED')",
            name="ck_clinical_access_session_status",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(status = 'REVOKED' AND revoked_at IS NOT NULL AND revocation_reason IS NOT NULL)",
            name="ck_clinical_access_session_revocation_state",
        ),
        CheckConstraint(
            "revocation_reason IS NULL OR revocation_reason IN ("
            "'PATIENT_REVOKED','PROVIDER_TRUST_LOST','PROVIDER_SESSION_ENDED',"
            "'PATIENT_MERGED','PATIENT_DELETED','PATIENT_ERASED',"
            "'CLAIM_FINALIZATION_FAILED','CAPABILITY_INVALIDATED','ADMINISTRATIVE')",
            name="ck_clinical_access_session_revocation_reason",
        ),
        Index(
            "ix_clinical_access_session_patient_active",
            "patient_id",
            "status",
            "expires_at",
        ),
        Index(
            "ix_clinical_access_session_provider_active",
            "provider_id",
            "hospital_id",
            "status",
            "expires_at",
        ),
        Index("ix_clinical_access_session_request", "consent_request_id"),
    )
