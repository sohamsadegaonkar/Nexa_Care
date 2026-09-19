from dataclasses import replace
from uuid import uuid4

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
from app.services.medication_catalog_policy import (
    MedicationCatalogEntryPolicyFacts,
    classifications_support_v1_universal,
    derive_v1_universal_allowed,
    required_evidence_dimensions,
)


def _facts() -> MedicationCatalogEntryPolicyFacts:
    preparer, first, second = uuid4(), uuid4(), uuid4()
    digest = "a" * 64
    return MedicationCatalogEntryPolicyFacts(
        terminology_status=TerminologyConceptStatus.ACTIVE,
        identity_granularity_sufficient=True,
        drug_schedule_class=DrugScheduleClass.NONE_CONFIRMED,
        ndps_class=NdpsClass.NOT_CONTROLLED_CONFIRMED,
        telemedicine_class=TelemedicineClass.LIST_O_ANY_MODE,
        special_recordkeeping_class=SpecialRecordkeepingClass.NONE_CONFIRMED,
        nexa_high_risk_class=NexaHighRiskClass.NONE_CONFIRMED,
        regulatory_product_status=RegulatoryProductStatus.CURRENT,
        evidence_dimensions=required_evidence_dimensions(),
        candidate_digest=digest,
        preparer_provider_id=preparer,
        first_reviewer_provider_id=first,
        second_reviewer_provider_id=second,
        first_review_digest=digest,
        second_review_digest=digest,
    )


def test_synthetic_universal_candidate_requires_every_frozen_dimension() -> None:
    facts = _facts()
    assert classifications_support_v1_universal(facts) is True
    assert derive_v1_universal_allowed(facts) is True


def test_schedule_g_h_h1_x_and_unknown_are_denied() -> None:
    for value in (
        DrugScheduleClass.G,
        DrugScheduleClass.H,
        DrugScheduleClass.H1,
        DrugScheduleClass.X,
        DrugScheduleClass.MULTIPLE_RESTRICTED,
        DrugScheduleClass.UNKNOWN,
    ):
        assert (
            derive_v1_universal_allowed(
                replace(_facts(), drug_schedule_class=value)
            )
            is False
        )


def test_ndps_unknown_restricted_mode_high_risk_and_inactive_are_denied() -> None:
    denials = (
        replace(_facts(), ndps_class=NdpsClass.CONTROLLED),
        replace(_facts(), ndps_class=NdpsClass.UNKNOWN),
        replace(_facts(), telemedicine_class=TelemedicineClass.RESTRICTED_MODE),
        replace(_facts(), telemedicine_class=TelemedicineClass.PROHIBITED),
        replace(_facts(), telemedicine_class=TelemedicineClass.UNKNOWN),
        replace(
            _facts(),
            special_recordkeeping_class=SpecialRecordkeepingClass.REQUIRED,
        ),
        replace(_facts(), nexa_high_risk_class=NexaHighRiskClass.SPECIALIST_RESTRICTED),
        replace(_facts(), nexa_high_risk_class=NexaHighRiskClass.ONCOLOGY_HIGH_RISK),
        replace(_facts(), nexa_high_risk_class=NexaHighRiskClass.OTHER_HIGH_RISK),
        replace(_facts(), regulatory_product_status=RegulatoryProductStatus.INACTIVE),
        replace(_facts(), regulatory_product_status=RegulatoryProductStatus.PROHIBITED),
        replace(_facts(), regulatory_product_status=RegulatoryProductStatus.UNKNOWN),
        replace(_facts(), terminology_status=TerminologyConceptStatus.INACTIVE),
        replace(_facts(), terminology_status=TerminologyConceptStatus.UNKNOWN),
        replace(_facts(), identity_granularity_sufficient=False),
    )
    assert all(derive_v1_universal_allowed(item) is False for item in denials)


def test_incomplete_evidence_is_denied() -> None:
    facts = _facts()
    missing = facts.evidence_dimensions - {
        MedicationEvidenceDimension.REGULATORY_PRODUCT_STATUS
    }
    assert (
        derive_v1_universal_allowed(
            replace(facts, evidence_dimensions=frozenset(missing))
        )
        is False
    )


def test_positive_review_separation_and_digest_binding_are_mandatory() -> None:
    facts = _facts()
    assert derive_v1_universal_allowed(
        replace(facts, first_reviewer_provider_id=None, first_review_digest=None)
    ) is False
    assert derive_v1_universal_allowed(
        replace(
            facts,
            second_reviewer_provider_id=facts.first_reviewer_provider_id,
        )
    ) is False
    assert derive_v1_universal_allowed(
        replace(
            facts,
            first_reviewer_provider_id=facts.preparer_provider_id,
        )
    ) is False
    assert derive_v1_universal_allowed(
        replace(facts, second_review_digest="b" * 64)
    ) is False


def test_no_real_medication_fixture_is_encoded_in_policy_tests() -> None:
    source = __import__("pathlib").Path(__file__).read_text(encoding="utf-8")
    assert "paracetamol" not in source.lower()
    assert "pregabalin" not in source.lower()
    assert "methylphenidate" not in source.lower()
