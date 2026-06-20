"""Pydantic context object returned by provider authentication."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.provider import AffiliationType


class ProviderIdentityContext(BaseModel):
    """Authenticated provider profile — no credential material."""

    model_config = ConfigDict(frozen=True)

    provider_id: UUID = Field(..., description="Primary key of provider_identity")
    display_name: str = Field(..., description="Professional display name")
    medical_registration_number: str | None = Field(
        default=None,
        description="Medical council registration number, if registered",
    )
    specialty: str | None = Field(default=None, description="Primary clinical specialty")
    contact_email: str = Field(..., description="Provider contact email (login identifier)")


class HospitalContext(BaseModel):
    """Hospital facility the provider is operating under for this request."""

    model_config = ConfigDict(frozen=True)

    hospital_id: UUID = Field(..., description="Primary key of hospital_registry")
    facility_code: str = Field(..., description="Internal facility code")
    display_name: str = Field(..., description="Human-readable facility name")


class AffiliationContext(BaseModel):
    """Resolved provider–hospital affiliation for the current request."""

    model_config = ConfigDict(frozen=True)

    affiliation_id: UUID = Field(
        ...,
        description="Primary key of provider_hospital_affiliation",
    )
    affiliation_type: AffiliationType = Field(
        ...,
        description="Nature of the provider's association with the hospital",
    )
    department: str | None = Field(default=None, description="Department within the facility")
    roles: list[str] = Field(default_factory=list, description="Granted roles at this facility")
    is_primary: bool = Field(..., description="Whether this is the provider's primary affiliation")
    valid_from: datetime | None = Field(default=None, description="Affiliation start, if bounded")
    valid_until: datetime | None = Field(default=None, description="Affiliation end, if bounded")


class ProviderContext(BaseModel):
    """Secure, immutable context for an authenticated provider request.

    Returned by ``get_provider_context`` after validating credentials against
    ``provider_credential`` and resolving the active hospital affiliation.
    """

    model_config = ConfigDict(frozen=True)

    provider: ProviderIdentityContext
    hospital: HospitalContext
    affiliation: AffiliationContext

    @property
    def actor_uid(self) -> str:
        """Stable audit-ledger actor identifier for this provider."""

        return str(self.provider.provider_id)
