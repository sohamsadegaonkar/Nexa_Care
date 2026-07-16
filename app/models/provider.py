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

    affiliations: Mapped[list["ProviderHospitalAffiliation"]] = relationship(
        back_populates="hospital",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_hospital_registry_is_active", "is_active"),
        Index("ix_hospital_registry_facility_code", "facility_code"),
    )


class ProviderIdentity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Individual doctor / provider profile (no patient data)."""

    __tablename__ = "provider_identity"

    provider_uid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
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
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True, unique=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
    )


class ProviderCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Authentication material for a provider identity.

    Password hashes and MFA configuration live here — never in
    ``provider_identity`` — so credential rotation does not touch profile data.
    """

    __tablename__ = "provider_credential"

    provider_uid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    mfa_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_identity.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    login_identifier: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
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
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    provider: Mapped[ProviderIdentity] = relationship(back_populates="credential")

    __table_args__ = (
        Index("ix_provider_credential_provider_uid", "provider_uid"),
        Index("ix_provider_credential_login_identifier", "login_identifier"),
        Index("ix_provider_credential_is_active", "is_active"),
    )
