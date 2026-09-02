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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
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
    # The lifecycle policy emits the next generation; it does not apply it yet.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    provider: Mapped[ProviderIdentity] = relationship(
        back_populates="professional_verification"
    )

    __table_args__ = (
        UniqueConstraint(
            "registration_authority_code",
            "registration_number_normalized",
            name="uq_professional_verification_authority_registration",
        ),
        Index("ix_professional_verification_status", "status"),
        Index("ix_professional_verification_provider_id", "provider_id"),
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
    # Stored generation only; transactional compare-and-swap is Phase 3E.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    facility: Mapped[HospitalRegistry] = relationship(back_populates="verification")

    __table_args__ = (
        Index("ix_facility_verification_status", "status"),
        Index("ix_facility_verification_facility_id", "facility_id"),
        CheckConstraint(
            "status IN ('DRAFT', 'PENDING_VERIFICATION', 'VERIFIED', "
            "'RECHECK_REQUIRED', 'SUSPENDED', 'REJECTED', 'CLOSED')",
            name="ck_facility_verification_status",
        ),
        CheckConstraint(
            "version > 0", name="ck_facility_verification_version_positive"
        ),
    )
