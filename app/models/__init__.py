"""ORM models and provider context types for Nexa Care V2."""

# --- 1. ALL IMPORTS MUST GO HERE AT THE VERY TOP ---
from app.models.ai_models import ExtractedMedicalDocument
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.consent_grant import ConsentGrantLog
from app.models.consent_ledger import ConsentLedger
from app.models.consent_sessions import ConsentSession
from app.models.dek_store import PatientDEKStore
from app.models.document_review import DocumentReviewQueue, DocumentReviewStatus
from app.models.identity_review import (
    IdentityReviewCaseRecord,
    IdentityReviewCaseRouteRecord,
    IdentityReviewDispositionRecord,
    IdentityReviewOperationRecord,
)
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_device_keys import PatientDeviceKey
from app.models.patient_policy import PatientPolicy
from app.models.patient_records import (
    Allergy,
    DocumentReference,
    LabResult,
    Medication,
    PatientRecord,
    TimelineEvent,
    Vitals,
)
from app.models.patient_tombstone import PatientTombstone
from app.models.pipeline import (
    AdjudicationCaseRecord,
    AdjudicationSubmissionRecord,
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionFailureQuarantineRecord,
    ExtractionDecisionRecord,
    ExtractedFieldRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
    FieldCorrection,
    PipelineCommit,
    ReviewQueueItem,
)
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
    "IdentityReviewCaseRecord",
    "IdentityReviewCaseRouteRecord",
    "IdentityReviewDispositionRecord",
    "IdentityReviewOperationRecord",
    "PatientDEKStore",
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "NFCCardRegistry",
    "NFCCardStatus",
    "Patient",
    "PatientAuthIdentity",
    "PatientDeviceKey",
    "PatientPolicy",
    "PatientRecord",
    "Vitals",
    "Medication",
    "LabResult",
    "Allergy",
    "DocumentReference",
    "TimelineEvent",
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
    "DocumentStorage",
    "AdjudicationCaseRecord",
    "AdjudicationSubmissionRecord",
    "ExtractionJob",
    "ExtractionCandidateRecord",
    "ExtractionFailureQuarantineRecord",
    "ExtractionDecisionRecord",
    "ExtractionRoutingRecord",
    "ExtractedFieldRecord",
    "FieldCorrection",
    "PipelineCommit",
    "ReviewQueueItem",
]
