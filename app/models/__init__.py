"""ORM models and provider context types for Nexa Care V2."""

# --- 1. ALL IMPORTS MUST GO HERE AT THE VERY TOP ---
from app.models.ai_models import ExtractedMedicalDocument
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.consent_grant import ConsentGrantLog
from app.models.consent_ledger import ConsentLedger
from app.models.consent_sessions import ConsentSession
from app.models.document_review import DocumentReviewQueue, DocumentReviewStatus
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus
from app.models.patient import Patient
from app.models.patient_policy import PatientPolicy
from app.models.patient_tombstone import PatientTombstone
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
from app.models.shards import NexaClinical, NexaEmergencySnapshot, NexaVault

# --- 2. EXECUTABLE CODE/VARIABLES GO DOWN HERE AT THE BOTTOM ---
__all__ = [
    "ExtractedMedicalDocument",
    "ConsentGrantLog",
    "ConsentLedger",
    "ConsentSession",
    "DocumentReviewQueue",
    "DocumentReviewStatus",
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "NFCCardRegistry",
    "NFCCardStatus",
    "Patient",
    "PatientPolicy",
    "PatientTombstone",
    "AffiliationType",
    "HospitalRegistry",
    "ProviderCredential",
    "ProviderHospitalAffiliation",
    "ProviderIdentity",
    "AffiliationContext",
    "HospitalContext",
    "ProviderContext",
    "ProviderIdentityContext",
    "NexaEmergencySnapshot",
    "NexaClinical",
    "NexaVault",
]