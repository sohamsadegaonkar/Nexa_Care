"""Exhaustive unit contract for the pure Phase-3C provider trust policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
from uuid import uuid4

import pytest

from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerificationStatus,
    ProfessionalVerificationStatus,
    VerificationSourceFailureReason,
    FacilityVerification,
    HospitalRegistry,
    ProfessionalVerification,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityService,
    ContactAssurancePolicy,
    InteractiveClinicalAuthentication,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.services.provider_trust_lifecycle import (
    AffiliationTransitionCommand,
    AffiliationTransitionFacts,
    FacilityTransitionCommand,
    FacilityTransitionFacts,
    LifecyclePolicyError,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
    plan_affiliation_transition,
    plan_facility_transition,
    plan_professional_transition,
)


NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)


def _professional_facts() -> ProfessionalTransitionFacts:
    return ProfessionalTransitionFacts(
        registration_authority_code="TEST-COUNCIL",
        registration_number_normalized="REG-001",
        verification_method="AUTHORITATIVE_LOOKUP",
        verification_source="TEST_REGISTRY",
        verification_reference="REFERENCE-001",
        identity_binding_method="REGISTRY_MATCH",
        identity_binding_status="CONFIRMED",
        registration_valid_from=NOW - timedelta(days=1),
        registration_valid_until=NOW + timedelta(days=30),
        next_review_at=NOW + timedelta(days=7),
        reviewer_id="reviewer-001",
        decision_reason_code="TEST_REASON",
        recheck_attempted_at=NOW - timedelta(minutes=1),
        recheck_failure_reason=VerificationSourceFailureReason.SOURCE_UNAVAILABLE,
        grace_expires_at=NOW + timedelta(hours=1),
        previous_verification_valid=True,
    )


def _facts_for_professional_command(
    command: ProfessionalTransitionCommand,
) -> ProfessionalTransitionFacts:
    if command is ProfessionalTransitionCommand.SUBMIT:
        return ProfessionalTransitionFacts(
            registration_authority_code="TEST-COUNCIL",
            registration_number_normalized="REG-001",
        )
    if command is ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE:
        return ProfessionalTransitionFacts(
            recheck_attempted_at=NOW - timedelta(minutes=1),
            recheck_failure_reason=VerificationSourceFailureReason.SOURCE_RESPONSE_INVALID,
            grace_expires_at=None,
            previous_verification_valid=True,
            authoritative_adverse_signal_at=NOW - timedelta(minutes=1),
        )
    return _professional_facts()


def _facility_facts() -> FacilityTransitionFacts:
    return FacilityTransitionFacts(
        verification_method="AUTHORITATIVE_LOOKUP",
        verification_source="TEST_FACILITY_REGISTRY",
        verification_reference="FACILITY-REFERENCE-001",
        next_review_at=NOW + timedelta(days=7),
        reviewer_id="reviewer-001",
        decision_reason_code="TEST_REASON",
    )


def _affiliation_facts() -> AffiliationTransitionFacts:
    return AffiliationTransitionFacts(
        decision_reason_code="TEST_REASON",
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=30),
    )


_PROFESSIONAL_ALLOWED = {
    (
        ProfessionalVerificationStatus.NOT_SUBMITTED,
        ProfessionalTransitionCommand.SUBMIT,
    ): (
        ProfessionalVerificationStatus.PENDING_REVIEW,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_SUBMITTED,
    ),
    (
        ProfessionalVerificationStatus.PENDING_REVIEW,
        ProfessionalTransitionCommand.VERIFY,
    ): (
        ProfessionalVerificationStatus.VERIFIED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFIED,
    ),
    (
        ProfessionalVerificationStatus.PENDING_REVIEW,
        ProfessionalTransitionCommand.REJECT,
    ): (
        ProfessionalVerificationStatus.REJECTED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_REJECTED,
    ),
    (ProfessionalVerificationStatus.VERIFIED, ProfessionalTransitionCommand.SUSPEND): (
        ProfessionalVerificationStatus.SUSPENDED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_SUSPENDED,
    ),
    (ProfessionalVerificationStatus.SUSPENDED, ProfessionalTransitionCommand.RESTORE): (
        ProfessionalVerificationStatus.VERIFIED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_RESTORED,
    ),
    (
        ProfessionalVerificationStatus.VERIFIED,
        ProfessionalTransitionCommand.MARK_RECHECK_DUE,
    ): (
        ProfessionalVerificationStatus.RECHECK_DUE,
        ProviderTrustAuditEvent.PROVIDER_VERIFICATION_SOURCE_UNAVAILABLE,
    ),
    (
        ProfessionalVerificationStatus.RECHECK_DUE,
        ProfessionalTransitionCommand.COMPLETE_RECHECK,
    ): (
        ProfessionalVerificationStatus.VERIFIED,
        ProviderTrustAuditEvent.PROVIDER_REVERIFICATION_PERFORMED,
    ),
    (
        ProfessionalVerificationStatus.RECHECK_DUE,
        ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE,
    ): (
        ProfessionalVerificationStatus.RECHECK_DUE,
        ProviderTrustAuditEvent.PROVIDER_RECHECK_GRACE_CANCELLED,
    ),
    (
        ProfessionalVerificationStatus.RECHECK_DUE,
        ProfessionalTransitionCommand.MARK_STALE,
    ): (
        ProfessionalVerificationStatus.VERIFICATION_STALE,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_STALE,
    ),
    (ProfessionalVerificationStatus.VERIFIED, ProfessionalTransitionCommand.REVOKE): (
        ProfessionalVerificationStatus.REVOKED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_REVOKED,
    ),
    (
        ProfessionalVerificationStatus.RECHECK_DUE,
        ProfessionalTransitionCommand.REVOKE,
    ): (
        ProfessionalVerificationStatus.REVOKED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_REVOKED,
    ),
    (ProfessionalVerificationStatus.VERIFIED, ProfessionalTransitionCommand.EXPIRE): (
        ProfessionalVerificationStatus.EXPIRED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_EXPIRED,
    ),
    (
        ProfessionalVerificationStatus.RECHECK_DUE,
        ProfessionalTransitionCommand.EXPIRE,
    ): (
        ProfessionalVerificationStatus.EXPIRED,
        ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_EXPIRED,
    ),
}


_FACILITY_ALLOWED = {
    (FacilityVerificationStatus.DRAFT, FacilityTransitionCommand.SUBMIT): (
        FacilityVerificationStatus.PENDING_VERIFICATION,
        ProviderTrustAuditEvent.FACILITY_VERIFICATION_SUBMITTED,
    ),
    (
        FacilityVerificationStatus.PENDING_VERIFICATION,
        FacilityTransitionCommand.VERIFY,
    ): (FacilityVerificationStatus.VERIFIED, ProviderTrustAuditEvent.FACILITY_VERIFIED),
    (
        FacilityVerificationStatus.PENDING_VERIFICATION,
        FacilityTransitionCommand.REJECT,
    ): (FacilityVerificationStatus.REJECTED, ProviderTrustAuditEvent.FACILITY_REJECTED),
    (FacilityVerificationStatus.VERIFIED, FacilityTransitionCommand.SUSPEND): (
        FacilityVerificationStatus.SUSPENDED,
        ProviderTrustAuditEvent.FACILITY_SUSPENDED,
    ),
    (FacilityVerificationStatus.SUSPENDED, FacilityTransitionCommand.RESTORE): (
        FacilityVerificationStatus.VERIFIED,
        ProviderTrustAuditEvent.FACILITY_RESTORED,
    ),
    (
        FacilityVerificationStatus.VERIFIED,
        FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
    ): (
        FacilityVerificationStatus.RECHECK_REQUIRED,
        ProviderTrustAuditEvent.FACILITY_RECHECK_REQUIRED,
    ),
    (
        FacilityVerificationStatus.RECHECK_REQUIRED,
        FacilityTransitionCommand.COMPLETE_RECHECK,
    ): (FacilityVerificationStatus.VERIFIED, ProviderTrustAuditEvent.FACILITY_VERIFIED),
    (FacilityVerificationStatus.VERIFIED, FacilityTransitionCommand.CLOSE): (
        FacilityVerificationStatus.CLOSED,
        ProviderTrustAuditEvent.FACILITY_CLOSED,
    ),
    (FacilityVerificationStatus.SUSPENDED, FacilityTransitionCommand.CLOSE): (
        FacilityVerificationStatus.CLOSED,
        ProviderTrustAuditEvent.FACILITY_CLOSED,
    ),
    (FacilityVerificationStatus.RECHECK_REQUIRED, FacilityTransitionCommand.CLOSE): (
        FacilityVerificationStatus.CLOSED,
        ProviderTrustAuditEvent.FACILITY_CLOSED,
    ),
}


_AFFILIATION_ALLOWED = {
    (
        AffiliationTrustStatus.PENDING_ACTIVATION,
        AffiliationTransitionCommand.ACTIVATE,
    ): (AffiliationTrustStatus.ACTIVE, ProviderTrustAuditEvent.AFFILIATION_ACTIVATED),
    (AffiliationTrustStatus.ACTIVE, AffiliationTransitionCommand.SUSPEND): (
        AffiliationTrustStatus.SUSPENDED,
        ProviderTrustAuditEvent.AFFILIATION_SUSPENDED,
    ),
    (AffiliationTrustStatus.SUSPENDED, AffiliationTransitionCommand.RESTORE): (
        AffiliationTrustStatus.ACTIVE,
        ProviderTrustAuditEvent.AFFILIATION_RESTORED,
    ),
    (AffiliationTrustStatus.ACTIVE, AffiliationTransitionCommand.REVOKE): (
        AffiliationTrustStatus.REVOKED,
        ProviderTrustAuditEvent.AFFILIATION_REVOKED,
    ),
    (AffiliationTrustStatus.SUSPENDED, AffiliationTransitionCommand.REVOKE): (
        AffiliationTrustStatus.REVOKED,
        ProviderTrustAuditEvent.AFFILIATION_REVOKED,
    ),
    (AffiliationTrustStatus.ACTIVE, AffiliationTransitionCommand.EXPIRE): (
        AffiliationTrustStatus.EXPIRED,
        ProviderTrustAuditEvent.AFFILIATION_EXPIRED,
    ),
    (AffiliationTrustStatus.SUSPENDED, AffiliationTransitionCommand.EXPIRE): (
        AffiliationTrustStatus.EXPIRED,
        ProviderTrustAuditEvent.AFFILIATION_EXPIRED,
    ),
    (AffiliationTrustStatus.ACTIVE, AffiliationTransitionCommand.LEAVE): (
        AffiliationTrustStatus.LEFT,
        ProviderTrustAuditEvent.AFFILIATION_LEFT,
    ),
    (AffiliationTrustStatus.SUSPENDED, AffiliationTransitionCommand.LEAVE): (
        AffiliationTrustStatus.LEFT,
        ProviderTrustAuditEvent.AFFILIATION_LEFT,
    ),
}


@pytest.mark.parametrize("state", list(ProfessionalVerificationStatus))
@pytest.mark.parametrize("command", list(ProfessionalTransitionCommand))
def test_professional_state_command_matrix_is_closed(state, command) -> None:
    expected = _PROFESSIONAL_ALLOWED.get((state, command))
    if expected is None:
        with pytest.raises(LifecyclePolicyError) as error:
            plan_professional_transition(
                state,
                command,
                _facts_for_professional_command(command),
                NOW,
                current_version=1,
            )
        assert error.value.code == "LIFECYCLE_TRANSITION_NOT_ALLOWED"
        return
    plan = plan_professional_transition(
        state, command, _facts_for_professional_command(command), NOW, current_version=1
    )
    assert (plan.new_state, plan.event_type) == (expected[0].value, expected[1])
    if command is ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE:
        assert plan.old_state == plan.new_state
    else:
        assert plan.old_state != plan.new_state
    assert (plan.expected_version, plan.next_version) == (1, 2)


@pytest.mark.parametrize("state", list(FacilityVerificationStatus))
@pytest.mark.parametrize("command", list(FacilityTransitionCommand))
def test_facility_state_command_matrix_is_closed(state, command) -> None:
    expected = _FACILITY_ALLOWED.get((state, command))
    if expected is None:
        with pytest.raises(LifecyclePolicyError) as error:
            plan_facility_transition(
                state, command, _facility_facts(), NOW, current_version=1
            )
        assert error.value.code == "LIFECYCLE_TRANSITION_NOT_ALLOWED"
        return
    plan = plan_facility_transition(
        state, command, _facility_facts(), NOW, current_version=1
    )
    assert (plan.new_state, plan.event_type) == (expected[0].value, expected[1])
    assert plan.old_state != plan.new_state


@pytest.mark.parametrize("state", list(AffiliationTrustStatus))
@pytest.mark.parametrize("command", list(AffiliationTransitionCommand))
def test_affiliation_state_command_matrix_is_closed(state, command) -> None:
    expected = _AFFILIATION_ALLOWED.get((state, command))
    if expected is None:
        with pytest.raises(LifecyclePolicyError) as error:
            plan_affiliation_transition(
                state, command, _affiliation_facts(), NOW, current_version=1
            )
        assert error.value.code == "LIFECYCLE_TRANSITION_NOT_ALLOWED"
        return
    plan = plan_affiliation_transition(
        state, command, _affiliation_facts(), NOW, current_version=1
    )
    assert (plan.new_state, plan.event_type) == (expected[0].value, expected[1])
    assert plan.old_state != plan.new_state


def test_terminal_professional_states_have_no_outgoing_transition() -> None:
    for state in (
        ProfessionalVerificationStatus.REJECTED,
        ProfessionalVerificationStatus.REVOKED,
        ProfessionalVerificationStatus.EXPIRED,
        ProfessionalVerificationStatus.VERIFICATION_STALE,
    ):
        for command in ProfessionalTransitionCommand:
            with pytest.raises(LifecyclePolicyError):
                plan_professional_transition(
                    state, command, _professional_facts(), NOW, current_version=1
                )


def test_terminal_facility_and_affiliation_states_have_no_outgoing_transition() -> None:
    for state in (
        FacilityVerificationStatus.REJECTED,
        FacilityVerificationStatus.CLOSED,
    ):
        for command in FacilityTransitionCommand:
            with pytest.raises(LifecyclePolicyError):
                plan_facility_transition(
                    state, command, _facility_facts(), NOW, current_version=1
                )
    for state in (
        AffiliationTrustStatus.REVOKED,
        AffiliationTrustStatus.EXPIRED,
        AffiliationTrustStatus.LEFT,
    ):
        for command in AffiliationTransitionCommand:
            with pytest.raises(LifecyclePolicyError):
                plan_affiliation_transition(
                    state, command, _affiliation_facts(), NOW, current_version=1
                )


def test_professional_recheck_grace_requires_complete_authoritative_evidence() -> None:
    facts = _professional_facts()
    plan = plan_professional_transition(
        ProfessionalVerificationStatus.VERIFIED,
        ProfessionalTransitionCommand.MARK_RECHECK_DUE,
        facts,
        NOW,
        current_version=3,
    )
    assert (
        plan.event_type
        is ProviderTrustAuditEvent.PROVIDER_VERIFICATION_SOURCE_UNAVAILABLE
    )
    assert plan.next_version == 4
    for invalid in (
        ProfessionalTransitionFacts(
            **{**facts.__dict__, "previous_verification_valid": False}
        ),
        ProfessionalTransitionFacts(
            **{**facts.__dict__, "recheck_failure_reason": None}
        ),
        ProfessionalTransitionFacts(
            **{**facts.__dict__, "grace_expires_at": NOW + timedelta(days=2)}
        ),
        ProfessionalTransitionFacts(
            **{**facts.__dict__, "authoritative_adverse_signal_at": NOW}
        ),
    ):
        with pytest.raises(LifecyclePolicyError):
            plan_professional_transition(
                ProfessionalVerificationStatus.VERIFIED,
                ProfessionalTransitionCommand.MARK_RECHECK_DUE,
                invalid,
                NOW,
                current_version=3,
            )


def test_professional_submission_rejects_reviewer_or_verification_facts() -> None:
    facts = ProfessionalTransitionFacts(
        registration_authority_code="TEST-COUNCIL",
        registration_number_normalized="REG-001",
        reviewer_id="forged-reviewer",
    )
    with pytest.raises(LifecyclePolicyError) as error:
        plan_professional_transition(
            ProfessionalVerificationStatus.NOT_SUBMITTED,
            ProfessionalTransitionCommand.SUBMIT,
            facts,
            NOW,
            current_version=1,
        )
    assert error.value.code == "LIFECYCLE_SUBMISSION_FACTS_INVALID"


def test_professional_submission_clears_all_reviewer_owned_artifacts() -> None:
    plan = plan_professional_transition(
        ProfessionalVerificationStatus.NOT_SUBMITTED,
        ProfessionalTransitionCommand.SUBMIT,
        _facts_for_professional_command(ProfessionalTransitionCommand.SUBMIT),
        NOW,
        current_version=1,
    )
    assert {
        "verification_method",
        "verification_source",
        "verification_reference",
        "identity_binding_method",
        "identity_binding_status",
        "registration_valid_from",
        "registration_valid_until",
        "verified_at",
        "last_checked_at",
        "next_review_at",
        "grace_expires_at",
        "recheck_attempted_at",
        "recheck_failure_reason",
        "authoritative_adverse_signal_at",
        "reviewer_id",
        "decision_reason_code",
    } <= plan.clears


def test_plans_are_immutable_and_cannot_modify_roles_or_capabilities() -> None:
    plan = plan_affiliation_transition(
        AffiliationTrustStatus.PENDING_ACTIVATION,
        AffiliationTransitionCommand.ACTIVATE,
        _affiliation_facts(),
        NOW,
        current_version=1,
    )
    with pytest.raises(Exception):
        plan.next_version = 8  # type: ignore[misc]
    fields = {update.field for update in plan.updates} | set(plan.clears)
    assert fields.isdisjoint({"roles", "capabilities", "provider_id", "hospital_id"})


def test_typed_commands_and_versions_fail_closed() -> None:
    with pytest.raises(LifecyclePolicyError) as invalid_command:
        plan_professional_transition(
            ProfessionalVerificationStatus.NOT_SUBMITTED,
            FacilityTransitionCommand.SUBMIT,
            _professional_facts(),
            NOW,
            current_version=1,
        )  # type: ignore[arg-type]
    assert invalid_command.value.code == "LIFECYCLE_COMMAND_INVALID"
    with pytest.raises(LifecyclePolicyError) as invalid_version:
        plan_affiliation_transition(
            AffiliationTrustStatus.PENDING_ACTIVATION,
            AffiliationTransitionCommand.ACTIVATE,
            _affiliation_facts(),
            NOW,
            current_version=0,
        )
    assert invalid_version.value.code == "LIFECYCLE_VERSION_INVALID"


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _TrustDb:
    def __init__(self, provider: ProviderIdentity, hospital: HospitalRegistry) -> None:
        self.values = [provider, hospital]

    async def execute(self, _statement: object) -> _Result:
        return _Result(self.values.pop(0))


def _eligible_graph():
    provider_id, hospital_id = uuid4(), uuid4()
    provider = ProviderIdentity(
        id=provider_id,
        provider_uid=str(provider_id),
        status="active",
        is_active=True,
        contact_email="provider@example.test",
        contact_phone="9876543210",
        email_verified_at=NOW - timedelta(days=1),
        phone_verified_at=NOW - timedelta(days=1),
    )
    provider.credential = ProviderCredential(
        provider_id=provider_id,
        login_identifier="provider@example.test",
        password_hash="test",
        is_active=True,
        mfa_enabled=True,
    )
    provider.professional_verification = ProfessionalVerification(
        provider_id=provider_id,
        status=ProfessionalVerificationStatus.VERIFIED.value,
        verified_at=NOW - timedelta(days=1),
        registration_valid_until=NOW + timedelta(days=30),
        next_review_at=NOW + timedelta(days=7),
    )
    hospital = HospitalRegistry(
        id=hospital_id,
        facility_code="TEST",
        legal_name="Test",
        display_name="Test",
        is_active=True,
    )
    hospital.verification = FacilityVerification(
        facility_id=hospital_id,
        status=FacilityVerificationStatus.VERIFIED.value,
        verified_at=NOW - timedelta(days=1),
        next_review_at=NOW + timedelta(days=7),
    )
    affiliation = ProviderHospitalAffiliation(
        id=uuid4(),
        provider_id=provider_id,
        hospital_id=hospital_id,
        roles=["clinician"],
        trust_status=AffiliationTrustStatus.ACTIVE.value,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=7),
    )
    provider.affiliations = [affiliation]
    return provider, hospital


def _eligibility(provider: ProviderIdentity, hospital: HospitalRegistry):
    return asyncio.run(
        ClinicalEligibilityService(
            contact_assurance_policy=ContactAssurancePolicy(
                True, True, "test-contact-v1"
            ),
            recent_mfa_max_age_seconds=lambda: 600,
        ).evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            InteractiveClinicalAuthentication(
                provider.id,
                hospital.id,
                ClinicalAuthenticationMethod.PROVIDER_SESSION,
                True,
                NOW - timedelta(seconds=1),
            ),
            ClinicalCapability.RECORD_READ,
            now=NOW,
        )
    )


@pytest.mark.parametrize("state", list(ProfessionalVerificationStatus))
def test_eligibility_remains_fail_closed_for_professional_lifecycle_states(
    state,
) -> None:
    provider, hospital = _eligible_graph()
    verification = provider.professional_verification
    verification.status = state.value
    if state is ProfessionalVerificationStatus.RECHECK_DUE:
        verification.previous_verification_valid = True
        verification.recheck_attempted_at = NOW - timedelta(minutes=1)
        verification.recheck_failure_reason = (
            VerificationSourceFailureReason.SOURCE_UNAVAILABLE.value
        )
        verification.grace_expires_at = NOW + timedelta(minutes=5)
    result = _eligibility(provider, hospital)
    assert result.allowed is (
        state
        in {
            ProfessionalVerificationStatus.VERIFIED,
            ProfessionalVerificationStatus.RECHECK_DUE,
        }
    )


@pytest.mark.parametrize("state", list(FacilityVerificationStatus))
def test_eligibility_remains_fail_closed_for_facility_lifecycle_states(state) -> None:
    provider, hospital = _eligible_graph()
    hospital.verification.status = state.value
    result = _eligibility(provider, hospital)
    assert result.allowed is (state is FacilityVerificationStatus.VERIFIED)


@pytest.mark.parametrize("state", list(AffiliationTrustStatus))
def test_eligibility_remains_fail_closed_for_affiliation_lifecycle_states(
    state,
) -> None:
    provider, hospital = _eligible_graph()
    provider.affiliations[0].trust_status = state.value
    result = _eligibility(provider, hospital)
    assert result.allowed is (state is AffiliationTrustStatus.ACTIVE)
