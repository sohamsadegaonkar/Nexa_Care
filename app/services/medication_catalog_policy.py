"""Pure medication-catalog safety policy.

This module contains no database, network, signing, or clinical-prescription
logic. Unknown/unproven facts fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.models.medication_catalog import (
    DrugScheduleClass,
    MedicationEvidenceDimension,
    NdpsClass,
    NexaHighRiskClass,
    RegulatoryProductStatus,
    SpecialRecordkeepingClass,
    TelemedicineClass,
    TerminologyConceptStatus,
)

MEDICATION_CATALOG_POLICY_VERSION = "medication-catalog/v1"

_REQUIRED_EVIDENCE_DIMENSIONS = frozenset(
    {
        MedicationEvidenceDimension.IDENTITY,
        MedicationEvidenceDimension.DRUG_SCHEDULE,
        MedicationEvidenceDimension.NDPS,
        MedicationEvidenceDimension.TELEMEDICINE,
        MedicationEvidenceDimension.SPECIAL_RECORDKEEPING,
        MedicationEvidenceDimension.HIGH_RISK,
        MedicationEvidenceDimension.REGULATORY_PRODUCT_STATUS,
    }
)


@dataclass(frozen=True, slots=True)
class MedicationCatalogEntryPolicyFacts:
    terminology_status: TerminologyConceptStatus
    identity_granularity_sufficient: bool
    drug_schedule_class: DrugScheduleClass
    ndps_class: NdpsClass
    telemedicine_class: TelemedicineClass
    special_recordkeeping_class: SpecialRecordkeepingClass
    nexa_high_risk_class: NexaHighRiskClass
    regulatory_product_status: RegulatoryProductStatus
    evidence_dimensions: frozenset[MedicationEvidenceDimension]
    candidate_digest: str
    preparer_provider_id: UUID
    first_reviewer_provider_id: UUID | None
    second_reviewer_provider_id: UUID | None
    first_review_digest: str | None
    second_review_digest: str | None


def required_evidence_dimensions() -> frozenset[MedicationEvidenceDimension]:
    return _REQUIRED_EVIDENCE_DIMENSIONS


def derive_v1_universal_allowed(
    facts: MedicationCatalogEntryPolicyFacts,
) -> bool:
    """Return the sole server-owned v1 universal/CARE_MODE_UNKNOWN decision."""

    if facts.terminology_status is not TerminologyConceptStatus.ACTIVE:
        return False
    if not facts.identity_granularity_sufficient:
        return False
    if facts.drug_schedule_class is not DrugScheduleClass.NONE_CONFIRMED:
        return False
    if facts.ndps_class is not NdpsClass.NOT_CONTROLLED_CONFIRMED:
        return False
    if facts.telemedicine_class is not TelemedicineClass.LIST_O_ANY_MODE:
        return False
    if (
        facts.special_recordkeeping_class
        is not SpecialRecordkeepingClass.NONE_CONFIRMED
    ):
        return False
    if facts.nexa_high_risk_class is not NexaHighRiskClass.NONE_CONFIRMED:
        return False
    if facts.regulatory_product_status is not RegulatoryProductStatus.CURRENT:
        return False
    if not _REQUIRED_EVIDENCE_DIMENSIONS.issubset(facts.evidence_dimensions):
        return False

    first = facts.first_reviewer_provider_id
    second = facts.second_reviewer_provider_id
    if first is None or second is None:
        return False
    if first == second:
        return False
    if first == facts.preparer_provider_id or second == facts.preparer_provider_id:
        return False
    if facts.first_review_digest != facts.candidate_digest:
        return False
    if facts.second_review_digest != facts.candidate_digest:
        return False
    return True
