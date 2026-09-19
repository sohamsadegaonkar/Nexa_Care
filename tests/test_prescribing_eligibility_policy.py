from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.provider import (
    PrescribingEligibilityDecision,
    PrescribingEligibilitySourceType,
    PrescribingEligibilityStatus,
    PrescribingPractitionerClass,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
)
from app.services.prescribing_eligibility_policy import (
    PRESCRIBER_ELIGIBILITY_POLICY_VERSION,
    current_prescribing_eligibility_denial,
)


NOW = datetime(2026, 9, 19, 12, 30, tzinfo=timezone.utc)


def _professional() -> ProfessionalVerification:
    provider_id = uuid4()
    return ProfessionalVerification(
        id=uuid4(),
        provider_id=provider_id,
        status=ProfessionalVerificationStatus.VERIFIED.value,
        registration_authority_code="NMC",
        registration_number_normalized="NMC-QUAL-1001",
        identity_binding_status="MATCHED",
        verified_at=NOW - timedelta(days=1),
        registration_valid_from=NOW - timedelta(days=100),
        registration_valid_until=NOW + timedelta(days=180),
        next_review_at=NOW + timedelta(days=20),
        authoritative_adverse_signal_at=None,
        version=7,
    )


def _decision(
    professional: ProfessionalVerification,
    *,
    status: PrescribingEligibilityStatus = PrescribingEligibilityStatus.ELIGIBLE,
    source_type: PrescribingEligibilitySourceType = PrescribingEligibilitySourceType.NMR,
    practitioner_class: PrescribingPractitionerClass = (
        PrescribingPractitionerClass.FULL_RMP_MODERN_MEDICINE
    ),
    version: int = 1,
) -> PrescribingEligibilityDecision:
    return PrescribingEligibilityDecision(
        id=uuid4(),
        provider_id=professional.provider_id,
        professional_verification_id=professional.id,
        professional_verification_version=professional.version,
        version=version,
        status=status.value,
        practitioner_class=practitioner_class.value,
        source_type=source_type.value,
        registration_authority_code=professional.registration_authority_code,
        registration_number_normalized=professional.registration_number_normalized,
        source_reference="NMR:QUAL-1001",
        evidence_sha256="a" * 64,
        checked_at=NOW - timedelta(minutes=2),
        valid_until=NOW + timedelta(days=10),
        reviewer_provider_id=uuid4(),
        decision_reason_code="PRIMARY_SOURCE_CURRENT_FULL_RMP",
        restriction_code=None,
        policy_version=PRESCRIBER_ELIGIBILITY_POLICY_VERSION,
        previous_decision_id=None,
    )


def test_current_full_rmp_decision_is_positive() -> None:
    professional = _professional()
    decision = _decision(professional)
    assert (
        current_prescribing_eligibility_denial(
            professional,
            decision,
            now=NOW,
        )
        is None
    )


def test_professional_version_drift_invalidates_older_positive_decision() -> None:
    professional = _professional()
    decision = _decision(professional)
    professional.version += 1
    assert current_prescribing_eligibility_denial(
        professional, decision, now=NOW
    ) == "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"


def test_professional_suspension_overrides_positive_decision() -> None:
    professional = _professional()
    decision = _decision(professional)
    professional.status = ProfessionalVerificationStatus.SUSPENDED.value
    assert current_prescribing_eligibility_denial(
        professional, decision, now=NOW
    ) == "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"


def test_expired_positive_decision_fails_closed() -> None:
    professional = _professional()
    decision = _decision(professional)
    decision.valid_until = NOW
    assert current_prescribing_eligibility_denial(
        professional, decision, now=NOW
    ) == "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"


def test_hpr_only_identity_cannot_become_positive() -> None:
    professional = _professional()
    decision = _decision(
        professional,
        source_type=PrescribingEligibilitySourceType.HPR,
    )
    assert current_prescribing_eligibility_denial(
        professional, decision, now=NOW
    ) == "PRESCRIBING_ELIGIBILITY_RESTRICTED"


def test_limited_practitioner_class_cannot_become_positive() -> None:
    professional = _professional()
    decision = _decision(
        professional,
        practitioner_class=PrescribingPractitionerClass.COMMUNITY_HEALTH_PROVIDER,
    )
    assert current_prescribing_eligibility_denial(
        professional, decision, now=NOW
    ) == "PRESCRIBING_ELIGIBILITY_RESTRICTED"


def test_registration_snapshot_drift_fails_closed() -> None:
    professional = _professional()
    decision = _decision(professional)
    decision.registration_number_normalized = "NMC-DIFFERENT"
    assert current_prescribing_eligibility_denial(
        professional, decision, now=NOW
    ) == "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"


def test_source_unavailable_decision_never_grants() -> None:
    professional = _professional()
    decision = _decision(
        professional,
        status=PrescribingEligibilityStatus.SOURCE_UNAVAILABLE,
    )
    assert current_prescribing_eligibility_denial(
        professional, decision, now=NOW
    ) == "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
