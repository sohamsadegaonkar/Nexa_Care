"""ORM models and provider context types for Nexa Care V2."""

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.provider import (
    AffiliationType,
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.models.nfc import (
    NFCCardEvent,
    NFCCardEventType,
    NFCCardRegistry,
    NFCCardSourceType,
    NFCCardStatus,
    NFCCardType,
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
    "NFCCardEvent",
    "NFCCardEventType",
    "NFCCardRegistry",
    "NFCCardSourceType",
    "NFCCardStatus",
    "NFCCardType",
    "ProviderContext",
    "ProviderCredential",
    "ProviderHospitalAffiliation",
    "ProviderIdentity",
    "ProviderIdentityContext",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
]
