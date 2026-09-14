"""Durable privacy-preserving patient-search identifier authority.

The table intentionally stores only keyed exact-match fingerprints.  Raw or
normalized PII is never persisted here.  A search identifier is not patient
authentication, consent, or clinical-access authority.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PatientSearchIdentifier(Base):
    """One lifecycle-versioned exact-match identifier bound to a patient.

    ``value_hmac`` is a domain-separated HMAC produced by server code with a
    dedicated discovery-index key.  The normalized identifier value must never
    be stored in this table, logs, audit metadata, URLs, or Redis keys.
    """

    __tablename__ = "patient_search_identifiers"
    __table_args__ = (
        CheckConstraint(
            "identifier_type IN ('PHONE')",
            name="ck_patient_search_identifier_type",
        ),
        CheckConstraint(
            "normalization_version > 0 AND key_version > 0",
            name="ck_patient_search_identifier_versions",
        ),
        CheckConstraint(
            "char_length(value_hmac) = 64",
            name="ck_patient_search_identifier_hmac_length",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revocation_reason IN "
            "('SUPERSEDED','IDENTITY_REVOKED','IDENTITY_REBOUND','PATIENT_ERASED',"
            "'AUTHORITY_CONFLICT','SOURCE_REVERIFICATION_FAILED','ADMINISTRATIVE'))",
            name="ck_patient_search_identifier_lifecycle",
        ),
        Index(
            "ix_patient_search_identifier_patient",
            "patient_id",
            "identifier_type",
        ),
        Index(
            "ix_patient_search_identifier_identity",
            "identity_id",
        ),
        Index(
            "uq_patient_search_identifier_active_patient_type",
            "patient_id",
            "identifier_type",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "uq_patient_search_identifier_active_value",
            "identifier_type",
            "key_version",
            "value_hmac",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    identifier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.patient_uuid", ondelete="RESTRICT"),
        nullable=False,
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_auth_identities.identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    identifier_type: Mapped[str] = mapped_column(String(16), nullable=False)
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    value_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
