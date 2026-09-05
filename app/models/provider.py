"""Provider-layer ORM models for Nexa Care V2.

These tables model individual clinicians and their hospital affiliations.
Provider identity data lives here — never mixed with patient PII or clinical
shards. Authentication secrets are isolated in ``provider_credential``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AffiliationType(str, enum.Enum):
    """How a provider is associated with a hospital facility."""

    PERMANENT = "permanent"
    VISITING = "visiting"
    TELEMEDICINE = "telemedicine"
    LOCUM = "locum"


class ProfessionalVerificationStatus(str, enum.Enum):
    """Authoritative professional-verification lifecycle states."""

    NOT_SUBMITTED = "NOT_SUBMITTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    VERIFIED = "VERIFIED"
    RECHECK_DUE = "RECHECK_DUE"
    VERIFICATION_STALE = "VERIFICATION_STALE"
    SUSPENDED = "SUSPENDED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class FacilityVerificationStatus(str, enum.Enum):
    """Facility trust is independent from provider professional trust."""

    DRAFT = "DRAFT"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    RECHECK_REQUIRED = "RECHECK_REQUIRED"
    SUSPENDED = "SUSPENDED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class AffiliationTrustStatus(str, enum.Enum):
    """Authoritative lifecycle for a provider-to-facility relationship."""

    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    LEFT = "LEFT"


class VerificationSourceFailureReason(str, enum.Enum):
    """Only SOURCE_UNAVAILABLE can support a bounded recheck grace."""

    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_RESPONSE_INVALID = "SOURCE_RESPONSE_INVALID"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class VerificationEvidenceOrigin(str, enum.Enum):
    """Server-owned provenance for an evidence observation."""

    MANUAL_REVIEWER_ATTESTATION = "MANUAL_REVIEWER_ATTESTATION"
    SERVER_REGISTRY_OBSERVATION = "SERVER_REGISTRY_OBSERVATION"


class VerificationEvidenceLookupPurpose(str, enum.Enum):
    """Purpose of an external verification or review check."""

    INITIAL_VERIFICATION = "INITIAL_VERIFICATION"
    RECHECK = "RECHECK"
    ADVERSE_SIGNAL_CHECK = "ADVERSE_SIGNAL_CHECK"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class VerificationEvidenceOutcome(str, enum.Enum):
    """Normalized observation outcome from external source or manual review."""

    CONFIRMED_ACTIVE = "CONFIRMED_ACTIVE"
    CONFIRMED_INACTIVE = "CONFIRMED_INACTIVE"
    NOT_FOUND = "NOT_FOUND"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    AMBIGUOUS = "AMBIGUOUS"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_RESPONSE_INVALID = "SOURCE_RESPONSE_INVALID"
    SOURCE_AUTHENTICATION_FAILURE = "SOURCE_AUTHENTICATION_FAILURE"
    SOURCE_INTEGRITY_FAILURE = "SOURCE_INTEGRITY_FAILURE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class VerificationIdentityBindingResult(str, enum.Enum):
    """Evidence-level identity binding outcome."""

    NOT_EVALUATED = "NOT_EVALUATED"
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


class VerificationReviewWorkStatus(str, enum.Enum):
    """Work item status for manual verification review queue."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class VerificationReviewWorkDisposition(str, enum.Enum):
    """Governed disposition for verification review work queue items."""

    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    SYSTEM_FAIL_CLOSED_AND_REVIEW = "SYSTEM_FAIL_CLOSED_AND_REVIEW"
    LIFECYCLE_SEMANTIC_GAP = "LIFECYCLE_SEMANTIC_GAP"


class VerificationWorkStatus(str, enum.Enum):
    """Lifecycle status of a provider verification work item."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    EXHAUSTED = "EXHAUSTED"
    CANCELLED_STALE = "CANCELLED_STALE"
    CANCELLED_POLICY = "CANCELLED_POLICY"
    FAILED_TERMINAL = "FAILED_TERMINAL"


class HospitalRegistry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Base hospital / facility registry entry."""

    __tablename__ = "hospital_registry"

    facility_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    facility_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    affiliations: Mapped[list["ProviderHospitalAffiliation"]] = relationship(
        back_populates="hospital",
        cascade="all, delete-orphan",
    )
    verification: Mapped["FacilityVerification | None"] = relationship(
        back_populates="facility",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_hospital_registry_is_active", "is_active"),
        Index("ix_hospital_registry_facility_code", "facility_code"),
    )


class ProviderIdentity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Individual doctor / provider profile (no patient data)."""

    __tablename__ = "provider_identity"

    provider_uid: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_registry.id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="provider")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    medical_registration_number: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
    )
    specialty: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, unique=True
    )
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    credential: Mapped["ProviderCredential | None"] = relationship(
        back_populates="provider",
        uselist=False,
        cascade="all, delete-orphan",
    )
    affiliations: Mapped[list["ProviderHospitalAffiliation"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    professional_verification: Mapped["ProfessionalVerification | None"] = relationship(
        back_populates="provider",
        uselist=False,
        cascade="all, delete-orphan",
    )
    contact_verification_challenges: Mapped[
        list["ProviderContactVerificationChallenge"]
    ] = relationship(back_populates="provider", cascade="all, delete-orphan")
    trust_permission_grants: Mapped[list["ProviderTrustPermissionGrant"]] = (
        relationship(back_populates="provider", cascade="all, delete-orphan")
    )

    __table_args__ = (
        Index("ix_provider_identity_provider_uid", "provider_uid"),
        Index("ix_provider_identity_hospital_id", "hospital_id"),
        Index("ix_provider_identity_status", "status"),
        Index("ix_provider_identity_is_active", "is_active"),
        Index("ix_provider_identity_contact_email", "contact_email"),
    )


class ProviderHospitalAffiliation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Junction linking a provider to one or more hospital facilities."""

    __tablename__ = "provider_hospital_affiliation"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_registry.id", ondelete="CASCADE"),
        nullable=False,
    )
    affiliation_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AffiliationType.PERMANENT.value,
    )
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trust_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AffiliationTrustStatus.PENDING_ACTIVATION.value,
    )

    # Stored generation only; transactional compare-and-swap is Phase 3E.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    provider: Mapped[ProviderIdentity] = relationship(back_populates="affiliations")
    hospital: Mapped[HospitalRegistry] = relationship(back_populates="affiliations")

    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "hospital_id",
            name="uq_provider_hospital_affiliation",
        ),
        Index("ix_provider_hospital_affiliation_provider_id", "provider_id"),
        Index("ix_provider_hospital_affiliation_hospital_id", "hospital_id"),
        Index("ix_provider_hospital_affiliation_is_active", "is_active"),
        CheckConstraint(
            "trust_status IN ('PENDING_ACTIVATION', 'ACTIVE', 'SUSPENDED', "
            "'REVOKED', 'EXPIRED', 'LEFT')",
            name="ck_provider_hospital_affiliation_trust_status",
        ),
        CheckConstraint(
            "version > 0", name="ck_provider_hospital_affiliation_version_positive"
        ),
    )


class ProviderTrustPermissionGrant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Authoritative organizational trust grant, separate from clinical roles.

    Active-grant uniqueness is released only by an explicit ``revoked_at``.
    An expired but non-revoked row is unusable for authorization yet deliberately
    retains its uniqueness slot, so a future re-grant flow must first apply an
    explicit, governed revocation or supersession transaction.
    """

    __tablename__ = "provider_trust_permission_grant"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_registry.id", ondelete="RESTRICT"),
        nullable=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by_actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    governance_reference: Mapped[str | None] = mapped_column(String(128))

    provider: Mapped[ProviderIdentity] = relationship(
        back_populates="trust_permission_grants"
    )

    __table_args__ = (
        CheckConstraint(
            "permission IN ('PROFESSIONAL_REVIEW', 'FACILITY_REVIEW', 'AFFILIATION_MANAGE', 'TRUST_PERMISSION_MANAGE')",
            name="ck_provider_trust_permission_grant_permission",
        ),
        CheckConstraint(
            "scope_type IN ('GLOBAL', 'FACILITY')",
            name="ck_provider_trust_permission_grant_scope_type",
        ),
        CheckConstraint(
            "(permission IN ('PROFESSIONAL_REVIEW', 'TRUST_PERMISSION_MANAGE') AND scope_type = 'GLOBAL' AND facility_id IS NULL) OR (permission IN ('FACILITY_REVIEW', 'AFFILIATION_MANAGE') AND scope_type = 'FACILITY' AND facility_id IS NOT NULL)",
            name="ck_provider_trust_permission_grant_scope_binding",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_provider_trust_permission_grant_validity",
        ),
        Index(
            "uq_provider_trust_permission_grant_global_active",
            "provider_id",
            "permission",
            unique=True,
            postgresql_where=(scope_type == "GLOBAL") & revoked_at.is_(None),
        ),
        Index(
            "uq_provider_trust_permission_grant_facility_active",
            "provider_id",
            "permission",
            "facility_id",
            unique=True,
            postgresql_where=(scope_type == "FACILITY") & revoked_at.is_(None),
        ),
        Index("ix_provider_trust_permission_grant_provider_id", "provider_id"),
        Index("ix_provider_trust_permission_grant_facility_id", "facility_id"),
    )


class ProviderContactVerificationChallenge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Authoritative, non-secret provider self-contact verification state.

    The contact and verifier are never duplicated here.  Their domain-separated
    HMAC fingerprints bind this one-time challenge to the exact canonical
    contact value that was current when it was issued.
    """

    __tablename__ = "provider_contact_verification_challenge"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_binding_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    failed_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    provider: Mapped[ProviderIdentity] = relationship(
        back_populates="contact_verification_challenges"
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('EMAIL', 'PHONE')",
            name="ck_provider_contact_challenge_channel",
        ),
        CheckConstraint(
            "max_attempts > 0 AND failed_attempt_count >= 0 "
            "AND failed_attempt_count <= max_attempts",
            name="ck_provider_contact_challenge_attempts",
        ),
        CheckConstraint(
            "(succeeded_at IS NULL) OR (consumed_at IS NOT NULL)",
            name="ck_provider_contact_challenge_success_consumed",
        ),
        Index(
            "ix_provider_contact_challenge_provider_channel_purpose",
            "provider_id",
            "channel",
            "purpose",
        ),
        Index("ix_provider_contact_challenge_expires_at", "expires_at"),
    )


class ProviderCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Authentication material for a provider identity.

    Password hashes and MFA configuration live here — never in
    ``provider_identity`` — so credential rotation does not touch profile data.
    """

    __tablename__ = "provider_credential"

    provider_uid: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    mfa_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    login_identifier: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True
    )
    # Canonical password hash. The legacy ``hashed_password`` column was
    # removed by 20260717_provider_pwd_canonical and must never be read.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    provider: Mapped[ProviderIdentity] = relationship(back_populates="credential")

    __table_args__ = (
        Index("ix_provider_credential_provider_uid", "provider_uid"),
        Index("ix_provider_credential_login_identifier", "login_identifier"),
        Index("ix_provider_credential_is_active", "is_active"),
    )


class ProfessionalVerification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Independent, reviewable professional trust evidence for one provider.

    ``ProviderIdentity.medical_registration_number`` remains a legacy claim.
    Only this record may contribute professional trust to clinical eligibility.
    """

    __tablename__ = "professional_verification"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    registration_authority_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    registration_number_normalized: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ProfessionalVerificationStatus.NOT_SUBMITTED.value,
    )
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_reference: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    identity_binding_method: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    identity_binding_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    registration_valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grace_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recheck_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recheck_failure_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    previous_verification_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    authoritative_adverse_signal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    server_provenance_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    # The lifecycle policy emits the next generation; it does not apply it yet.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    provider: Mapped[ProviderIdentity] = relationship(
        back_populates="professional_verification"
    )
    evidence: Mapped[list["ProviderTrustVerificationEvidence"]] = relationship(
        back_populates="professional_verification",
        foreign_keys="ProviderTrustVerificationEvidence.professional_verification_id",
        passive_deletes="all",
    )
    server_provenance_evidence: Mapped["ProviderTrustVerificationEvidence | None"] = (
        relationship(
            foreign_keys="[ProfessionalVerification.server_provenance_evidence_id, ProfessionalVerification.id]",
            primaryjoin="and_(ProfessionalVerification.server_provenance_evidence_id == ProviderTrustVerificationEvidence.id, ProfessionalVerification.id == ProviderTrustVerificationEvidence.professional_verification_id)",
        )
    )

    __table_args__ = (
        UniqueConstraint(
            "registration_authority_code",
            "registration_number_normalized",
            name="uq_professional_verification_authority_registration",
        ),
        ForeignKeyConstraint(
            ["server_provenance_evidence_id", "id"],
            [
                "provider_trust_verification_evidence.id",
                "provider_trust_verification_evidence.professional_verification_id",
            ],
            name="fk_professional_verification_server_provenance",
            ondelete="RESTRICT",
        ),
        Index("ix_professional_verification_status", "status"),
        Index("ix_professional_verification_provider_id", "provider_id"),
        Index(
            "ix_professional_verification_server_provenance_evidence_id",
            "server_provenance_evidence_id",
        ),
        CheckConstraint(
            "status IN ('NOT_SUBMITTED', 'PENDING_REVIEW', 'VERIFIED', "
            "'RECHECK_DUE', 'VERIFICATION_STALE', 'SUSPENDED', 'REJECTED', "
            "'REVOKED', 'EXPIRED')",
            name="ck_professional_verification_status",
        ),
        CheckConstraint(
            "version > 0", name="ck_professional_verification_version_positive"
        ),
    )


class FacilityVerification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Independent, reviewable trust evidence for a hospital facility."""

    __tablename__ = "facility_verification"

    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_registry.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FacilityVerificationStatus.DRAFT.value
    )
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_reference: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    registration_authority_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    registration_number_normalized: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    registration_valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grace_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recheck_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recheck_failure_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    previous_verification_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    authoritative_adverse_signal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    server_provenance_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    # Stored generation only; transactional compare-and-swap is Phase 3E.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    facility: Mapped[HospitalRegistry] = relationship(back_populates="verification")
    evidence: Mapped[list["ProviderTrustVerificationEvidence"]] = relationship(
        back_populates="facility_verification",
        foreign_keys="ProviderTrustVerificationEvidence.facility_verification_id",
        passive_deletes="all",
    )
    server_provenance_evidence: Mapped["ProviderTrustVerificationEvidence | None"] = (
        relationship(
            foreign_keys="[FacilityVerification.server_provenance_evidence_id, FacilityVerification.id]",
            primaryjoin="and_(FacilityVerification.server_provenance_evidence_id == ProviderTrustVerificationEvidence.id, FacilityVerification.id == ProviderTrustVerificationEvidence.facility_verification_id)",
        )
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["server_provenance_evidence_id", "id"],
            [
                "provider_trust_verification_evidence.id",
                "provider_trust_verification_evidence.facility_verification_id",
            ],
            name="fk_facility_verification_server_provenance",
            ondelete="RESTRICT",
        ),
        Index("ix_facility_verification_status", "status"),
        Index("ix_facility_verification_facility_id", "facility_id"),
        Index(
            "ix_facility_verification_server_provenance_evidence_id",
            "server_provenance_evidence_id",
        ),
        Index(
            "ix_facility_verification_registration",
            "registration_authority_code",
            "registration_number_normalized",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PENDING_VERIFICATION', 'VERIFIED', "
            "'RECHECK_REQUIRED', 'SUSPENDED', 'REJECTED', 'CLOSED')",
            name="ck_facility_verification_status",
        ),
        CheckConstraint(
            "version > 0", name="ck_facility_verification_version_positive"
        ),
        CheckConstraint(
            "registration_valid_until IS NULL OR registration_valid_from IS NULL "
            "OR registration_valid_until >= registration_valid_from",
            name="ck_facility_verification_validity",
        ),
        CheckConstraint(
            "recheck_failure_reason IS NULL OR recheck_failure_reason IN "
            "('SOURCE_UNAVAILABLE', 'SOURCE_RESPONSE_INVALID', 'SOURCE_NOT_FOUND', 'REVIEW_REQUIRED')",
            name="ck_facility_verification_recheck_failure_reason",
        ),
    )


class ProviderTrustVerificationEvidence(Base, UUIDPrimaryKeyMixin):
    """Immutable, append-only verification evidence observation.

    Relates to exactly one authoritative lifecycle resource:
    ProfessionalVerification OR FacilityVerification.
    It does not grant authority by itself; it records observation facts.
    """

    __tablename__ = "provider_trust_verification_evidence"

    professional_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("professional_verification.id", ondelete="RESTRICT"),
        nullable=True,
    )
    facility_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("facility_verification.id", ondelete="RESTRICT"),
        nullable=True,
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lookup_purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_reference: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    observed_valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    identity_binding_result: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=VerificationIdentityBindingResult.NOT_EVALUATED.value,
    )
    binding_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_transaction_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    observed_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    professional_verification: Mapped[ProfessionalVerification | None] = relationship(
        back_populates="evidence",
        foreign_keys=[professional_verification_id],
    )
    facility_verification: Mapped[FacilityVerification | None] = relationship(
        back_populates="evidence",
        foreign_keys=[facility_verification_id],
    )
    review_work: Mapped["ProviderTrustVerificationReviewWork | None"] = relationship(
        back_populates="evidence",
        uselist=False,
        passive_deletes="all",
    )

    __table_args__ = (
        CheckConstraint(
            "(professional_verification_id IS NOT NULL AND facility_verification_id IS NULL) "
            "OR (professional_verification_id IS NULL AND facility_verification_id IS NOT NULL)",
            name="ck_provider_trust_verification_evidence_resource_target",
        ),
        CheckConstraint(
            "origin IN ('MANUAL_REVIEWER_ATTESTATION', 'SERVER_REGISTRY_OBSERVATION')",
            name="ck_provider_trust_verification_evidence_origin",
        ),
        CheckConstraint(
            "lookup_purpose IN ('INITIAL_VERIFICATION', 'RECHECK', 'ADVERSE_SIGNAL_CHECK', 'MANUAL_REVIEW')",
            name="ck_provider_trust_verification_evidence_lookup_purpose",
        ),
        CheckConstraint(
            "outcome IN ('CONFIRMED_ACTIVE', 'CONFIRMED_INACTIVE', 'NOT_FOUND', "
            "'IDENTITY_MISMATCH', 'AMBIGUOUS', 'SOURCE_UNAVAILABLE', "
            "'SOURCE_RESPONSE_INVALID', 'SOURCE_AUTHENTICATION_FAILURE', "
            "'SOURCE_INTEGRITY_FAILURE', 'REVIEW_REQUIRED')",
            name="ck_provider_trust_verification_evidence_outcome",
        ),
        CheckConstraint(
            "identity_binding_result IN ('NOT_EVALUATED', 'MATCHED', 'MISMATCHED', 'AMBIGUOUS')",
            name="ck_provider_trust_verification_evidence_identity_binding_result",
        ),
        CheckConstraint(
            "observed_resource_version >= 1",
            name="ck_ptve_observed_resource_version",
        ),
        CheckConstraint(
            "(origin = 'SERVER_REGISTRY_OBSERVATION' AND adapter_version IS NOT NULL AND length(trim(adapter_version)) > 0) "
            "OR (origin = 'MANUAL_REVIEWER_ATTESTATION')",
            name="ck_provider_trust_verification_evidence_adapter_version_origin",
        ),
        CheckConstraint(
            "observed_valid_until IS NULL OR observed_valid_from IS NULL OR observed_valid_until >= observed_valid_from",
            name="ck_provider_trust_verification_evidence_validity_interval",
        ),
        CheckConstraint(
            "response_digest IS NULL OR response_digest ~ '^[0-9a-f]{64}$'",
            name="ck_provider_trust_verification_evidence_response_digest",
        ),
        CheckConstraint(
            "length(trim(source_id)) > 0",
            name="ck_provider_trust_verification_evidence_source_id_non_empty",
        ),
        UniqueConstraint(
            "id",
            "professional_verification_id",
            name="uq_evidence_professional_binding",
        ),
        UniqueConstraint(
            "id",
            "facility_verification_id",
            name="uq_evidence_facility_binding",
        ),
        Index(
            "ix_provider_trust_verification_evidence_prof_id",
            "professional_verification_id",
        ),
        Index(
            "ix_provider_trust_verification_evidence_fac_id",
            "facility_verification_id",
        ),
        Index("ix_provider_trust_verification_evidence_source_id", "source_id"),
        Index("ix_provider_trust_verification_evidence_observed_at", "observed_at"),
        Index("ix_provider_trust_verification_evidence_outcome", "outcome"),
    )


class ProviderTrustVerificationReviewWork(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Governed manual-review work queue item created when automated verification

    fails closed, requires human review, or encounters ambiguous/adverse registry findings.
    One review work item is bound to exactly one verification evidence row.
    """

    __tablename__ = "provider_trust_verification_review_work"

    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_trust_verification_evidence.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    disposition: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=VerificationReviewWorkStatus.OPEN.value,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    evidence: Mapped[ProviderTrustVerificationEvidence] = relationship(
        back_populates="review_work"
    )

    __table_args__ = (
        Index("ix_provider_trust_verification_review_work_status", "status"),
        Index("ix_provider_trust_verification_review_work_evidence_id", "evidence_id"),
        CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name="chk_review_work_status",
        ),
        CheckConstraint(
            "disposition IN ('HUMAN_REVIEW_REQUIRED', 'SYSTEM_FAIL_CLOSED_AND_REVIEW', 'LIFECYCLE_SEMANTIC_GAP')",
            name="chk_review_work_disposition",
        ),
        CheckConstraint(
            "length(trim(reason_code)) > 0",
            name="chk_review_work_reason_code_non_empty",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND resolved_at IS NULL AND resolved_by_actor_id IS NULL) "
            "OR (status = 'RESOLVED' AND resolved_at IS NOT NULL AND resolved_by_actor_id IS NOT NULL)",
            name="chk_review_work_resolution_integrity",
        ),
    )


class ProviderVerificationWork(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Durable work item for automated external registry verification.

    Maintains execution state, leases, retries, and result linkage for
    asynchronous background verification tasks.
    """

    __tablename__ = "provider_verification_work"

    professional_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("professional_verification.id", ondelete="RESTRICT"),
        nullable=True,
    )
    facility_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("facility_verification.id", ondelete="RESTRICT"),
        nullable=True,
    )
    lookup_purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    registration_authority_code: Mapped[str] = mapped_column(String(64), nullable=False)
    registration_number_normalized: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    expected_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduler_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=VerificationWorkStatus.PENDING.value,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_trust_verification_evidence.id", ondelete="RESTRICT"),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    professional_verification: Mapped[ProfessionalVerification | None] = relationship(
        foreign_keys=[professional_verification_id],
    )
    facility_verification: Mapped[FacilityVerification | None] = relationship(
        foreign_keys=[facility_verification_id],
    )
    result_evidence: Mapped[ProviderTrustVerificationEvidence | None] = relationship(
        foreign_keys=[result_evidence_id],
    )

    __table_args__ = (
        CheckConstraint(
            "(professional_verification_id IS NOT NULL AND facility_verification_id IS NULL) "
            "OR (professional_verification_id IS NULL AND facility_verification_id IS NOT NULL)",
            name="ck_pvw_resource_target_xor",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'COMPLETED', 'EXHAUSTED', "
            "'CANCELLED_STALE', 'CANCELLED_POLICY', 'FAILED_TERMINAL')",
            name="ck_pvw_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_pvw_attempts",
        ),
        CheckConstraint(
            "length(trim(source_id)) > 0",
            name="ck_pvw_source_id_non_empty",
        ),
        CheckConstraint(
            "length(trim(adapter_version)) > 0",
            name="ck_pvw_adapter_version_non_empty",
        ),
        CheckConstraint(
            "expected_resource_version >= 1",
            name="ck_pvw_expected_version_positive",
        ),
        Index(
            "uq_prof_active_verification_work",
            "professional_verification_id",
            "lookup_purpose",
            "source_id",
            "expected_resource_version",
            unique=True,
            postgresql_where=text(
                "professional_verification_id IS NOT NULL AND status IN ('PENDING', 'CLAIMED')"
            ),
        ),
        Index(
            "uq_fac_active_verification_work",
            "facility_verification_id",
            "lookup_purpose",
            "source_id",
            "expected_resource_version",
            unique=True,
            postgresql_where=text(
                "facility_verification_id IS NOT NULL AND status IN ('PENDING', 'CLAIMED')"
            ),
        ),
        Index(
            "ix_provider_verification_work_status_next_attempt",
            "status",
            "next_attempt_at",
            "priority",
        ),
        Index(
            "ix_provider_verification_work_prof_id",
            "professional_verification_id",
        ),
        Index(
            "ix_provider_verification_work_fac_id",
            "facility_verification_id",
        ),
        Index(
            "ix_provider_verification_work_lease_expires",
            "lease_expires_at",
        ),
        Index(
            "ix_provider_verification_work_result_evidence",
            "result_evidence_id",
        ),
    )
