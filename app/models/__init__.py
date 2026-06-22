"""Package marker for models module."""
"""ORM models and provider context types for Nexa Care V2."""

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus
from app.models.provider import (
    AffiliationType,
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)

__all__ = [
    "AffiliationContext",
    "AffiliationType",
    "Base",
    "HospitalContext",
    "HospitalRegistry",
    "NFCCardRegistry",
    "NFCCardStatus",
    "ProviderContext",
    "ProviderCredential",
    "ProviderHospitalAffiliation",
    "ProviderIdentity",
    "ProviderIdentityContext",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
]
