"""Patient legal document acceptance records.

Stores append-only evidence of patient acceptance of versioned legal
documents (Terms of Service, Privacy Notice).  These rows contain NO
patient PII and are NOT encrypted — they must remain durable compliance
evidence even after cryptographic erasure of the patient's profile DEK.

This is a SEPARATE domain from clinical consent (ConsentEngine/ConsentGrant).
Do not conflate legal acceptance with clinical consent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PatientLegalAcceptance(Base):
    """Append-only record of a patient accepting a versioned legal document.

    Allowed ``document_type`` values: ``TERMS_OF_SERVICE``, ``PRIVACY_NOTICE``.
    The ``document_version`` and ``document_sha256`` are always server-owned;
    the client may never supply these values.
    """

    __tablename__ = "patient_legal_acceptances"
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('TERMS_OF_SERVICE', 'PRIVACY_NOTICE')",
            name="ck_legal_acceptances_document_type",
        ),
        CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_legal_acceptances_sha256_hex",
        ),
        UniqueConstraint(
            "patient_id",
            "document_type",
            "document_version",
            name="uq_legal_acceptance_patient_doc_version",
        ),
        Index("ix_patient_legal_acceptances_patient_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
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
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
