"""Server-owned medication catalog authority models.

The catalog is global policy/reference data. It never contains patient clinical
state and never independently grants prescribing authority.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MedicationCatalogReleaseStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    QUALIFIED = "QUALIFIED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class DrugScheduleClass(str, enum.Enum):
    NONE_CONFIRMED = "NONE_CONFIRMED"
    G = "G"
    H = "H"
    H1 = "H1"
    X = "X"
    MULTIPLE_RESTRICTED = "MULTIPLE_RESTRICTED"
    UNKNOWN = "UNKNOWN"


class NdpsClass(str, enum.Enum):
    NOT_CONTROLLED_CONFIRMED = "NOT_CONTROLLED_CONFIRMED"
    CONTROLLED = "CONTROLLED"
    UNKNOWN = "UNKNOWN"


class TelemedicineClass(str, enum.Enum):
    LIST_O_ANY_MODE = "LIST_O_ANY_MODE"
    RESTRICTED_MODE = "RESTRICTED_MODE"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


class SpecialRecordkeepingClass(str, enum.Enum):
    NONE_CONFIRMED = "NONE_CONFIRMED"
    REQUIRED = "REQUIRED"
    UNKNOWN = "UNKNOWN"


class NexaHighRiskClass(str, enum.Enum):
    NONE_CONFIRMED = "NONE_CONFIRMED"
    SPECIALIST_RESTRICTED = "SPECIALIST_RESTRICTED"
    ONCOLOGY_HIGH_RISK = "ONCOLOGY_HIGH_RISK"
    OTHER_HIGH_RISK = "OTHER_HIGH_RISK"
    UNKNOWN = "UNKNOWN"


class RegulatoryProductStatus(str, enum.Enum):
    CURRENT = "CURRENT"
    INACTIVE = "INACTIVE"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


class TerminologyConceptStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


class MedicationEvidenceDimension(str, enum.Enum):
    IDENTITY = "IDENTITY"
    DRUG_SCHEDULE = "DRUG_SCHEDULE"
    NDPS = "NDPS"
    TELEMEDICINE = "TELEMEDICINE"
    SPECIAL_RECORDKEEPING = "SPECIAL_RECORDKEEPING"
    HIGH_RISK = "HIGH_RISK"
    REGULATORY_PRODUCT_STATUS = "REGULATORY_PRODUCT_STATUS"


class MedicationEvidenceAuthority(str, enum.Enum):
    CDSCO = "CDSCO"
    INDIA_CODE = "INDIA_CODE"
    MOHFW = "MOHFW"
    NMC = "NMC"
    NRCES = "NRCES"
    SNOMED_IDENTITY_ONLY = "SNOMED_IDENTITY_ONLY"


class MedicationEmergencyAction(str, enum.Enum):
    DENY = "DENY"
    CLEAR = "CLEAR"


class MedicationEmergencyReason(str, enum.Enum):
    REGULATORY_PROHIBITION = "REGULATORY_PROHIBITION"
    REGULATORY_RECLASSIFICATION = "REGULATORY_RECLASSIFICATION"
    SOURCE_INTEGRITY_FAILURE = "SOURCE_INTEGRITY_FAILURE"
    CATALOG_CLASSIFICATION_ERROR = "CATALOG_CLASSIFICATION_ERROR"
    PATIENT_SAFETY_HOLD = "PATIENT_SAFETY_HOLD"


class MedicationCatalogRelease(Base):
    __tablename__ = "medication_catalog_release"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=MedicationCatalogReleaseStatus.DRAFT.value,
        server_default=MedicationCatalogReleaseStatus.DRAFT.value,
    )
    source_cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_terminology_version: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    integrity_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_manifest: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_key_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_algorithm: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    prepared_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=False,
    )
    qualified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=True,
    )
    activated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    qualified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    previous_release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medication_catalog_release.id", ondelete="RESTRICT"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','QUALIFIED','ACTIVE','SUPERSEDED','REVOKED')",
            name="ck_medication_catalog_release_status",
        ),
        CheckConstraint(
            "signature_algorithm IS NULL OR signature_algorithm = 'ECDSA_SHA_256'",
            name="ck_medication_catalog_release_signature_algorithm",
        ),
        CheckConstraint(
            "integrity_digest IS NULL OR integrity_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_release_integrity_digest",
        ),
        CheckConstraint(
            "prepared_by <> COALESCE(qualified_by, "
            "'00000000-0000-0000-0000-000000000000'::uuid)",
            name="ck_medication_catalog_release_preparer_not_qualifier",
        ),
        CheckConstraint(
            "prepared_by <> COALESCE(activated_by, "
            "'00000000-0000-0000-0000-000000000000'::uuid)",
            name="ck_medication_catalog_release_preparer_not_activator",
        ),
        CheckConstraint(
            "previous_release_id IS NULL OR previous_release_id <> id",
            name="ck_medication_catalog_release_previous_not_self",
        ),
        CheckConstraint(
            "(status = 'DRAFT') OR "
            "(qualified_by IS NOT NULL AND qualified_at IS NOT NULL "
            "AND integrity_digest IS NOT NULL AND canonical_manifest IS NOT NULL "
            "AND artifact_signature IS NOT NULL AND artifact_key_id IS NOT NULL "
            "AND signature_algorithm = 'ECDSA_SHA_256')",
            name="ck_medication_catalog_release_published_shape",
        ),
        CheckConstraint(
            "(status <> 'ACTIVE') OR "
            "(activated_by IS NOT NULL AND activated_at IS NOT NULL "
            "AND superseded_at IS NULL AND revoked_at IS NULL)",
            name="ck_medication_catalog_release_active_shape",
        ),
        CheckConstraint(
            "(status <> 'SUPERSEDED') OR "
            "(superseded_at IS NOT NULL AND revoked_at IS NULL)",
            name="ck_medication_catalog_release_superseded_shape",
        ),
        CheckConstraint(
            "(status <> 'REVOKED') OR revoked_at IS NOT NULL",
            name="ck_medication_catalog_release_revoked_shape",
        ),
        Index(
            "uq_medication_catalog_release_single_active",
            "status",
            unique=True,
            postgresql_where=(status == MedicationCatalogReleaseStatus.ACTIVE.value),
        ),
        Index(
            "ix_medication_catalog_release_source_cutoff",
            "source_cutoff_at",
        ),
    )


class MedicationCatalogEntry(Base):
    __tablename__ = "medication_catalog_entry"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medication_catalog_release.id", ondelete="RESTRICT"),
        nullable=False,
    )
    medication_code: Mapped[str] = mapped_column(String(64), nullable=False)
    code_system: Mapped[str] = mapped_column(String(64), nullable=False)
    code_system_version: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_generic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    medication_display: Mapped[str] = mapped_column(String(255), nullable=False)
    ingredient_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    dose_form: Mapped[str | None] = mapped_column(String(128), nullable=True)
    identity_strength_descriptor: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    identity_granularity_sufficient: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    terminology_status: Mapped[str] = mapped_column(String(16), nullable=False)
    drug_schedule_class: Mapped[str] = mapped_column(String(32), nullable=False)
    ndps_class: Mapped[str] = mapped_column(String(32), nullable=False)
    telemedicine_class: Mapped[str] = mapped_column(String(32), nullable=False)
    special_recordkeeping_class: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    nexa_high_risk_class: Mapped[str] = mapped_column(String(32), nullable=False)
    regulatory_product_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    classification_rationale_code: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    v1_universal_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    entry_integrity_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    first_reviewer_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=True,
    )
    second_reviewer_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=True,
    )
    first_review_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    second_review_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    second_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "medication_code",
            name="uq_medication_catalog_entry_release_code",
        ),
        CheckConstraint(
            "terminology_status IN ('ACTIVE','INACTIVE','UNKNOWN')",
            name="ck_medication_catalog_entry_terminology_status",
        ),
        CheckConstraint(
            "drug_schedule_class IN "
            "('NONE_CONFIRMED','G','H','H1','X','MULTIPLE_RESTRICTED','UNKNOWN')",
            name="ck_medication_catalog_entry_schedule",
        ),
        CheckConstraint(
            "ndps_class IN ('NOT_CONTROLLED_CONFIRMED','CONTROLLED','UNKNOWN')",
            name="ck_medication_catalog_entry_ndps",
        ),
        CheckConstraint(
            "telemedicine_class IN "
            "('LIST_O_ANY_MODE','RESTRICTED_MODE','PROHIBITED','UNKNOWN')",
            name="ck_medication_catalog_entry_telemedicine",
        ),
        CheckConstraint(
            "special_recordkeeping_class IN "
            "('NONE_CONFIRMED','REQUIRED','UNKNOWN')",
            name="ck_medication_catalog_entry_recordkeeping",
        ),
        CheckConstraint(
            "nexa_high_risk_class IN "
            "('NONE_CONFIRMED','SPECIALIST_RESTRICTED','ONCOLOGY_HIGH_RISK',"
            "'OTHER_HIGH_RISK','UNKNOWN')",
            name="ck_medication_catalog_entry_high_risk",
        ),
        CheckConstraint(
            "regulatory_product_status IN ('CURRENT','INACTIVE','PROHIBITED','UNKNOWN')",
            name="ck_medication_catalog_entry_regulatory_status",
        ),
        CheckConstraint(
            "entry_integrity_digest IS NULL OR "
            "entry_integrity_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_entry_digest",
        ),
        CheckConstraint(
            "first_review_digest IS NULL OR "
            "first_review_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_entry_first_review_digest",
        ),
        CheckConstraint(
            "second_review_digest IS NULL OR "
            "second_review_digest ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_entry_second_review_digest",
        ),
        CheckConstraint(
            "first_reviewer_provider_id IS NULL OR "
            "second_reviewer_provider_id IS NULL OR "
            "first_reviewer_provider_id <> second_reviewer_provider_id",
            name="ck_medication_catalog_entry_distinct_reviewers",
        ),
        CheckConstraint(
            "(first_reviewer_provider_id IS NULL) = "
            "(first_reviewed_at IS NULL) AND "
            "(first_reviewer_provider_id IS NULL) = "
            "(first_review_digest IS NULL)",
            name="ck_medication_catalog_entry_first_review_shape",
        ),
        CheckConstraint(
            "(second_reviewer_provider_id IS NULL) = "
            "(second_reviewed_at IS NULL) AND "
            "(second_reviewer_provider_id IS NULL) = "
            "(second_review_digest IS NULL)",
            name="ck_medication_catalog_entry_second_review_shape",
        ),
        CheckConstraint(
            "length(trim(medication_code)) BETWEEN 1 AND 64",
            name="ck_medication_catalog_entry_code_nonempty",
        ),
        CheckConstraint(
            "length(trim(classification_rationale_code)) > 0",
            name="ck_medication_catalog_entry_rationale_nonempty",
        ),
        Index("ix_medication_catalog_entry_code", "medication_code"),
        Index(
            "ix_medication_catalog_entry_release_allowed",
            "release_id",
            "v1_universal_allowed",
        ),
    )


class MedicationCatalogEvidence(Base):
    __tablename__ = "medication_catalog_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medication_catalog_release.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medication_catalog_entry.id", ondelete="RESTRICT"),
        nullable=False,
    )
    finding_dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(32), nullable=False)
    source_document_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finding_value: Mapped[str] = mapped_column(String(128), nullable=False)
    rationale_code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prepared_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "entry_id",
            "finding_dimension",
            "evidence_sha256",
            name="uq_medication_catalog_evidence_finding",
        ),
        CheckConstraint(
            "finding_dimension IN "
            "('IDENTITY','DRUG_SCHEDULE','NDPS','TELEMEDICINE',"
            "'SPECIAL_RECORDKEEPING','HIGH_RISK','REGULATORY_PRODUCT_STATUS')",
            name="ck_medication_catalog_evidence_dimension",
        ),
        CheckConstraint(
            "source_authority IN "
            "('CDSCO','INDIA_CODE','MOHFW','NMC','NRCES','SNOMED_IDENTITY_ONLY')",
            name="ck_medication_catalog_evidence_authority",
        ),
        CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_evidence_sha256",
        ),
        CheckConstraint(
            "length(trim(source_reference)) > 0 AND "
            "length(trim(finding_value)) > 0 AND "
            "length(trim(rationale_code)) > 0",
            name="ck_medication_catalog_evidence_nonempty",
        ),
        Index(
            "ix_medication_catalog_evidence_entry",
            "release_id",
            "entry_id",
        ),
    )


class MedicationCatalogEmergencyDeny(Base):
    __tablename__ = "medication_catalog_emergency_deny"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    medication_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="RESTRICT"),
        nullable=False,
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    predecessor_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medication_catalog_emergency_deny.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "medication_code",
            "version",
            name="uq_medication_catalog_emergency_code_version",
        ),
        UniqueConstraint(
            "predecessor_event_id",
            name="uq_medication_catalog_emergency_predecessor",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_medication_catalog_emergency_version_positive",
        ),
        CheckConstraint(
            "action IN ('DENY','CLEAR')",
            name="ck_medication_catalog_emergency_action",
        ),
        CheckConstraint(
            "reason_code IN "
            "('REGULATORY_PROHIBITION','REGULATORY_RECLASSIFICATION',"
            "'SOURCE_INTEGRITY_FAILURE','CATALOG_CLASSIFICATION_ERROR',"
            "'PATIENT_SAFETY_HOLD')",
            name="ck_medication_catalog_emergency_reason",
        ),
        CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_medication_catalog_emergency_evidence_sha256",
        ),
        CheckConstraint(
            "predecessor_event_id IS NULL OR predecessor_event_id <> id",
            name="ck_medication_catalog_emergency_predecessor_not_self",
        ),
        Index(
            "ix_medication_catalog_emergency_code_effective",
            "medication_code",
            "effective_at",
            "version",
        ),
    )
