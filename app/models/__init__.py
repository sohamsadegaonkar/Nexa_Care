"""ORM models and provider context types for Nexa Care V2."""

# --- 1. ALL IMPORTS MUST GO HERE AT THE VERY TOP ---
from app.models.ai_models import ExtractedMedicalDocument
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.document_review import DocumentReviewQueue, DocumentReviewStatus
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

# --- 2. EXECUTABLE CODE/VARIABLES GO DOWN HERE AT THE BOTTOM ---
__all__ = [
    "ExtractedMedicalDocument",
    "DocumentReviewQueue",
    "DocumentReviewStatus",
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "NFCCardRegistry",
    "NFCCardStatus",
    "AffiliationType",
    "HospitalRegistry",
    "ProviderCredential",
    "ProviderHospitalAffiliation",
    "ProviderIdentity",
    "AffiliationContext",
    "HospitalContext",
    "ProviderContext",
    "ProviderIdentityContext",
]