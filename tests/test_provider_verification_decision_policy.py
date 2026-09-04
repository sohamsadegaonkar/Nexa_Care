"""Unit tests and architectural guards for pure verification decision policy (Phase 5D)."""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.provider import (
    FacilityVerificationStatus,
    ProfessionalVerificationStatus,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
    VerificationSourceFailureReason,
)
from app.services.provider_trust_lifecycle import (
    FacilityTransitionCommand,
    ProfessionalTransitionCommand,
)
from app.services.provider_verification_decision_policy import (
    OBSERVATION_REQUEST_BINDING_GAP,
    PROVIDER_VERIFICATION_DECISION_POLICY_VERSION,
    SYSTEM_ACTOR_PROVENANCE_GAP,
    FacilityVerificationContext,
    ProfessionalVerificationContext,
    VerificationDecisionDisposition,
    VerificationDecisionReason,
    VerificationPolicyError,
    VerificationPolicyInputError,
    evaluate_facility_observation,
    evaluate_professional_observation,
)
from app.services.provider_verification_registry import (
    FacilityLookupRequest,
    ProfessionalLookupRequest,
    RegistryObservation,
    RegistryResourceType,
)


def _make_prof_obs(
    outcome: VerificationEvidenceOutcome,
    purpose: VerificationEvidenceLookupPurpose = VerificationEvidenceLookupPurpose.RECHECK,
    binding: VerificationIdentityBindingResult = VerificationIdentityBindingResult.MATCHED,
    source_id: str = "TEST_SOURCE_ALPHA",
) -> RegistryObservation:
    return RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id=source_id,
        adapter_version="1.0.0",
        observed_at=datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc),
        lookup_purpose=purpose,
        outcome=outcome,
        identity_binding_result=binding,
    )


def _make_facility_obs(
    outcome: VerificationEvidenceOutcome,
    purpose: VerificationEvidenceLookupPurpose = VerificationEvidenceLookupPurpose.RECHECK,
    binding: VerificationIdentityBindingResult = VerificationIdentityBindingResult.MATCHED,
    source_id: str = "TEST_SOURCE_FACILITY",
) -> RegistryObservation:
    return RegistryObservation(
        resource_type=RegistryResourceType.FACILITY,
        source_id=source_id,
        adapter_version="1.0.0",
        observed_at=datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc),
        lookup_purpose=purpose,
        outcome=outcome,
        identity_binding_result=binding,
    )


def test_version_frozen() -> None:
    """The decision policy version is code-owned and frozen."""
    assert (
        PROVIDER_VERIFICATION_DECISION_POLICY_VERSION
        == "provider-verification-decision-policy/1.0"
    )


# ---------------------------------------------------------------------------
# Professional Initial Verification Test Matrix
# ---------------------------------------------------------------------------


def test_professional_initial_verification_human_gated() -> None:
    """Initial verification for professional is permanently human-gated in 5D."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.PENDING_REVIEW,
        current_version=1,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    # Even CONFIRMED_ACTIVE + MATCHED requires human review
    obs_active = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    plan = evaluate_professional_observation(
        observation=obs_active, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan.candidate_command is None
    assert (
        plan.reason_code
        == VerificationDecisionReason.INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED
    )
    assert plan.requires_human_review is True

    # NOT_FOUND on initial requires human review
    obs_nf = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.NOT_FOUND,
        purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    plan_nf = evaluate_professional_observation(
        observation=obs_nf, request=req, context=context, now=now
    )
    assert plan_nf.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan_nf.candidate_command is None
    assert plan_nf.requires_human_review is True

    # IDENTITY_MISMATCH on initial requires human review
    obs_mismatch = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.IDENTITY_MISMATCH,
        purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        binding=VerificationIdentityBindingResult.MISMATCHED,
    )
    plan_mm = evaluate_professional_observation(
        observation=obs_mismatch, request=req, context=context, now=now
    )
    assert plan_mm.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan_mm.requires_human_review is True


# ---------------------------------------------------------------------------
# Professional Positive Recheck Test Matrix
# ---------------------------------------------------------------------------


def test_professional_positive_recheck_automation_candidate() -> None:
    """RECHECK_DUE + RECHECK + CONFIRMED_ACTIVE + MATCHED + same server source is automation-eligible."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    future_validity = now + timedelta(days=365)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=3,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        registration_valid_until=future_validity,
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        binding=VerificationIdentityBindingResult.MATCHED,
        source_id="TEST_SOURCE_ALPHA",
    )

    plan = evaluate_professional_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert (
        plan.disposition == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
    )
    assert plan.candidate_command == ProfessionalTransitionCommand.COMPLETE_RECHECK
    assert plan.expected_resource_version == 3
    assert (
        plan.reason_code
        == VerificationDecisionReason.POSITIVE_RECHECK_AUTOMATION_ELIGIBLE
    )
    assert plan.requires_human_review is False
    assert plan.grace_expires_at is None


def test_professional_positive_recheck_requires_server_source_continuity() -> None:
    """Changing verification source requires human review."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    # Observation from different source
    obs_diff_source = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        source_id="TEST_SOURCE_BETA",
    )
    plan = evaluate_professional_observation(
        observation=obs_diff_source, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan.candidate_command is None
    assert plan.reason_code == VerificationDecisionReason.SOURCE_CONTINUITY_REQUIRED
    assert plan.requires_human_review is True

    # No established server provenance
    context_no_prov = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        server_provenance_established=False,
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        source_id="TEST_SOURCE_ALPHA",
    )
    plan_no_prov = evaluate_professional_observation(
        observation=obs, request=req, context=context_no_prov, now=now
    )
    assert (
        plan_no_prov.disposition
        == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    )
    assert (
        plan_no_prov.reason_code
        == VerificationDecisionReason.SOURCE_CONTINUITY_REQUIRED
    )


def test_professional_positive_recheck_blocked_by_adverse_signal_or_expiry() -> None:
    """Adverse signal or expired registration blocks automated COMPLETE_RECHECK."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    # Adverse signal present
    context_adverse = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
        authoritative_adverse_signal_at=now - timedelta(days=1),
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        source_id="TEST_SOURCE_ALPHA",
    )
    plan_adv = evaluate_professional_observation(
        observation=obs, request=req, context=context_adverse, now=now
    )
    assert plan_adv.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan_adv.reason_code == VerificationDecisionReason.ADVERSE_SIGNAL_PRESENT

    # Registration validity expired
    context_expired = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
        registration_valid_until=now - timedelta(hours=1),
    )
    plan_exp = evaluate_professional_observation(
        observation=obs, request=req, context=context_expired, now=now
    )
    assert plan_exp.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan_exp.reason_code == VerificationDecisionReason.REGISTRATION_EXPIRED


# ---------------------------------------------------------------------------
# Cross-Resource Reuse Protection Test Matrix
# ---------------------------------------------------------------------------


def test_cross_resource_identity_mismatch_denied() -> None:
    """Observation generated for a different registration number or authority cannot be reused."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG9999",  # Does NOT match context REG1001
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=1,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        source_id="TEST_SOURCE_ALPHA",
    )

    plan = evaluate_professional_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan.candidate_command is None
    assert plan.reason_code == VerificationDecisionReason.REGISTRATION_IDENTITY_MISMATCH
    assert plan.requires_human_review is True


# ---------------------------------------------------------------------------
# Professional Outage & Bounded Grace Test Matrix
# ---------------------------------------------------------------------------


def test_professional_verified_outage_with_grace() -> None:
    """VERIFIED + RECHECK + SOURCE_UNAVAILABLE + all prerequisites proposes bounded grace <= 24h."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    future_validity = now + timedelta(days=30)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        registration_valid_until=future_validity,
        previous_verification_valid=True,
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        source_id="TEST_SOURCE_ALPHA",
    )

    plan = evaluate_professional_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert (
        plan.disposition == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
    )
    assert plan.candidate_command == ProfessionalTransitionCommand.MARK_RECHECK_DUE
    assert (
        plan.reason_code == VerificationDecisionReason.SOURCE_UNAVAILABLE_BOUNDED_GRACE
    )
    assert plan.requires_human_review is False
    assert plan.grace_expires_at is not None
    assert plan.grace_expires_at == now + timedelta(hours=24)


def test_professional_outage_grace_bounded_by_registration_validity() -> None:
    """Grace cannot exceed registration_valid_until when validity is shorter than 24h."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    short_validity = now + timedelta(hours=6)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        registration_valid_until=short_validity,
        previous_verification_valid=True,
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        source_id="TEST_SOURCE_ALPHA",
    )

    plan = evaluate_professional_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert plan.grace_expires_at == short_validity


def test_professional_verified_outage_without_grace_prerequisites() -> None:
    """If grace prerequisites fail (e.g. not previously valid or no server prov), fail-closed without grace."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    # Missing previous_verification_valid
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        previous_verification_valid=False,
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
        registration_valid_until=now + timedelta(days=30),
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        source_id="TEST_SOURCE_ALPHA",
    )

    plan = evaluate_professional_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert (
        plan.disposition
        == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
    )
    assert plan.candidate_command == ProfessionalTransitionCommand.MARK_RECHECK_DUE
    assert (
        plan.reason_code
        == VerificationDecisionReason.SOURCE_UNAVAILABLE_NO_GRACE_FAIL_CLOSED
    )
    assert plan.grace_expires_at is None
    assert plan.requires_human_review is True


# ---------------------------------------------------------------------------
# Professional Non-Outage Failures Test Matrix
# ---------------------------------------------------------------------------


def test_professional_verified_non_outage_failures_never_receive_grace() -> None:
    """Non-outage source failures for VERIFIED state produce fail-closed without grace."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        previous_verification_valid=True,
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
        registration_valid_until=now + timedelta(days=30),
    )

    non_outage_outcomes = (
        VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID,
        VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE,
        VerificationEvidenceOutcome.SOURCE_INTEGRITY_FAILURE,
        VerificationEvidenceOutcome.NOT_FOUND,
        VerificationEvidenceOutcome.IDENTITY_MISMATCH,
        VerificationEvidenceOutcome.AMBIGUOUS,
        VerificationEvidenceOutcome.REVIEW_REQUIRED,
        VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
    )
    for outcome in non_outage_outcomes:
        obs = _make_prof_obs(outcome=outcome, source_id="TEST_SOURCE_ALPHA")
        plan = evaluate_professional_observation(
            observation=obs, request=req, context=context, now=now
        )
        assert (
            plan.disposition
            == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
        )
        assert plan.candidate_command == ProfessionalTransitionCommand.MARK_RECHECK_DUE
        assert (
            plan.reason_code
            == VerificationDecisionReason.SOURCE_FAILURE_FAIL_CLOSED_REVIEW
        )
        assert plan.grace_expires_at is None
        assert plan.requires_human_review is True


# ---------------------------------------------------------------------------
# Active Grace Semantic Gap & Repeated Outage Test Matrix
# ---------------------------------------------------------------------------


def test_active_grace_non_outage_failure_returns_semantic_gap() -> None:
    """During active grace, a later non-outage failure is a LIFECYCLE_SEMANTIC_GAP."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    active_grace = now + timedelta(hours=18)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=3,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        current_grace_expires_at=active_grace,
        previous_verification_valid=True,
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    # Non-outage adverse observation during active grace
    obs_fail = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE,
        source_id="TEST_SOURCE_ALPHA",
    )
    plan = evaluate_professional_observation(
        observation=obs_fail, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.LIFECYCLE_SEMANTIC_GAP
    assert plan.candidate_command is None
    assert (
        plan.reason_code
        == VerificationDecisionReason.RECHECK_GRACE_CANCELLATION_NOT_EXPRESSIBLE
    )
    assert plan.requires_human_review is True


def test_repeated_outage_preserves_existing_grace_without_extension() -> None:
    """Repeated SOURCE_UNAVAILABLE during active grace preserves grace and does not roll forward."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    existing_grace = now + timedelta(hours=12)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=3,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        current_grace_expires_at=existing_grace,
        previous_verification_valid=True,
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    obs_outage = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        source_id="TEST_SOURCE_ALPHA",
    )
    plan = evaluate_professional_observation(
        observation=obs_outage, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.NO_MUTATION_REQUIRED
    assert plan.candidate_command is None
    assert (
        plan.reason_code
        == VerificationDecisionReason.REPEATED_OUTAGE_EXISTING_GRACE_PRESERVED
    )
    assert plan.grace_expires_at == existing_grace  # Exactly preserved, NOT now + 24h
    assert plan.requires_human_review is False


# ---------------------------------------------------------------------------
# Professional Verified + Confirmed Active State Precondition Test
# ---------------------------------------------------------------------------


def test_professional_verified_confirmed_active_recheck_precondition() -> None:
    """Confirmed active RECHECK observation while already VERIFIED cannot execute COMPLETE_RECHECK."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        source_id="TEST_SOURCE_ALPHA",
    )

    plan = evaluate_professional_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan.candidate_command is None
    assert (
        plan.reason_code
        == VerificationDecisionReason.RECHECK_STATE_PRECONDITION_REQUIRED
    )
    assert plan.requires_human_review is True


def test_professional_verified_adverse_signal_check_confirmed_active() -> None:
    """ADVERSE_SIGNAL_CHECK confirming active while VERIFIED requires no mutation."""
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=2,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_ALPHA",
    )
    obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
        source_id="TEST_SOURCE_ALPHA",
    )

    plan = evaluate_professional_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.NO_MUTATION_REQUIRED
    assert plan.candidate_command is None
    assert (
        plan.reason_code
        == VerificationDecisionReason.ACTIVE_VERIFICATION_OBSERVATION_MATCH
    )
    assert plan.requires_human_review is False


# ---------------------------------------------------------------------------
# Facility Test Matrix
# ---------------------------------------------------------------------------


def test_facility_initial_verification_human_gated() -> None:
    """Facility initial verification is permanently human-gated in 5D."""
    req = FacilityLookupRequest(
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    context = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.PENDING_VERIFICATION,
        current_version=1,
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    obs = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )

    plan = evaluate_facility_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert plan.candidate_command is None
    assert (
        plan.reason_code
        == VerificationDecisionReason.INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED
    )


def test_facility_recheck_fails_closed_without_grace() -> None:
    """Facility grace remains permanently disabled. Outage or failure marks RECHECK_REQUIRED with no grace."""
    req = FacilityLookupRequest(
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    context = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.VERIFIED,
        current_version=2,
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_FACILITY",
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    # SOURCE_UNAVAILABLE: marks RECHECK_REQUIRED with NO grace
    obs_outage = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE
    )
    plan_outage = evaluate_facility_observation(
        observation=obs_outage, request=req, context=context, now=now
    )
    assert (
        plan_outage.disposition
        == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
    )
    assert (
        plan_outage.candidate_command == FacilityTransitionCommand.MARK_RECHECK_REQUIRED
    )
    assert plan_outage.grace_expires_at is None
    assert plan_outage.requires_human_review is True

    # Other failures: marks RECHECK_REQUIRED with NO grace
    obs_fail = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE
    )
    plan_fail = evaluate_facility_observation(
        observation=obs_fail, request=req, context=context, now=now
    )
    assert (
        plan_fail.disposition
        == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
    )
    assert (
        plan_fail.candidate_command == FacilityTransitionCommand.MARK_RECHECK_REQUIRED
    )
    assert plan_fail.grace_expires_at is None


def test_facility_positive_recheck_automation_candidate() -> None:
    """Facility RECHECK_REQUIRED + CONFIRMED_ACTIVE + same source is automation-eligible."""
    req = FacilityLookupRequest(
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    context = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.RECHECK_REQUIRED,
        current_version=3,
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_FACILITY",
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    obs = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        source_id="TEST_SOURCE_FACILITY",
    )

    plan = evaluate_facility_observation(
        observation=obs, request=req, context=context, now=now
    )
    assert (
        plan.disposition == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
    )
    assert plan.candidate_command == FacilityTransitionCommand.COMPLETE_RECHECK
    assert plan.expected_resource_version == 3
    assert plan.requires_human_review is False


def test_facility_recheck_required_negative_requires_human_review() -> None:
    """Already RECHECK_REQUIRED with negative/ambiguous observation requires human review (no auto CLOSE)."""
    req = FacilityLookupRequest(
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    context = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.RECHECK_REQUIRED,
        current_version=3,
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE_FACILITY",
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    for outcome in (
        VerificationEvidenceOutcome.NOT_FOUND,
        VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
    ):
        obs = _make_facility_obs(outcome=outcome, source_id="TEST_SOURCE_FACILITY")
        plan = evaluate_facility_observation(
            observation=obs, request=req, context=context, now=now
        )
        assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
        assert plan.candidate_command is None
        assert plan.requires_human_review is True


# ---------------------------------------------------------------------------
# Terminal and Suspended States Test Matrix
# ---------------------------------------------------------------------------


def test_terminal_and_suspended_states_never_automated() -> None:
    """Terminal and suspended states cannot be automated or resurrected."""
    req_prof = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    obs_active = _make_prof_obs(outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE)

    # Suspended professional
    ctx_susp = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.SUSPENDED,
        current_version=5,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
    )
    plan_susp = evaluate_professional_observation(
        observation=obs_active, request=req_prof, context=ctx_susp, now=now
    )
    assert (
        plan_susp.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    )
    assert (
        plan_susp.reason_code
        == VerificationDecisionReason.SUSPENDED_STATE_NO_AUTOMATION
    )

    # Revoked professional
    ctx_rev = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.REVOKED,
        current_version=6,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
    )
    plan_rev = evaluate_professional_observation(
        observation=obs_active, request=req_prof, context=ctx_rev, now=now
    )
    assert plan_rev.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    assert (
        plan_rev.reason_code == VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION
    )

    # Closed facility
    req_fac = FacilityLookupRequest(
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    ctx_closed = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.CLOSED,
        current_version=4,
        registration_authority_code="TEST_FAC_AUTH",
        registration_number_normalized="FAC500",
    )
    obs_fac = _make_facility_obs(outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE)
    plan_closed = evaluate_facility_observation(
        observation=obs_fac, request=req_fac, context=ctx_closed, now=now
    )
    assert (
        plan_closed.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
    )
    assert (
        plan_closed.reason_code
        == VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION
    )


# ---------------------------------------------------------------------------
# Static Authority Allowlist Test
# ---------------------------------------------------------------------------


def test_static_automation_candidate_command_allowlist() -> None:
    """Candidate commands for automation plans are strictly frozen to the defined allowlist."""
    allowed_prof_commands = {
        ProfessionalTransitionCommand.MARK_RECHECK_DUE,
        ProfessionalTransitionCommand.COMPLETE_RECHECK,
    }
    allowed_facility_commands = {
        FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
        FacilityTransitionCommand.COMPLETE_RECHECK,
    }

    # Forbidden professional commands must never be automated
    forbidden_prof = set(ProfessionalTransitionCommand) - allowed_prof_commands
    assert ProfessionalTransitionCommand.VERIFY in forbidden_prof
    assert ProfessionalTransitionCommand.RESTORE in forbidden_prof
    assert ProfessionalTransitionCommand.REJECT in forbidden_prof
    assert ProfessionalTransitionCommand.REVOKE in forbidden_prof
    assert ProfessionalTransitionCommand.EXPIRE in forbidden_prof
    assert ProfessionalTransitionCommand.MARK_STALE in forbidden_prof

    # Forbidden facility commands must never be automated
    forbidden_fac = set(FacilityTransitionCommand) - allowed_facility_commands
    assert FacilityTransitionCommand.VERIFY in forbidden_fac
    assert FacilityTransitionCommand.RESTORE in forbidden_fac
    assert FacilityTransitionCommand.REJECT in forbidden_fac
    assert FacilityTransitionCommand.CLOSE in forbidden_fac
    assert FacilityTransitionCommand.SUSPEND in forbidden_fac


# ---------------------------------------------------------------------------
# Input Validation & Sanitized Error Tests
# ---------------------------------------------------------------------------


def test_input_validation_and_sanitized_errors() -> None:
    """Invalid context or request parameters raise sanitized VerificationPolicyInputError."""
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    naive_time = datetime(2026, 9, 4, 12, 0, 0)  # naive

    # Naive now datetime
    req = ProfessionalLookupRequest(
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    context = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=1,
        registration_authority_code="TEST_PROF_AUTH",
        registration_number_normalized="REG1001",
    )
    obs = _make_prof_obs(outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE)

    with pytest.raises(
        VerificationPolicyInputError, match="now must be a timezone-aware datetime"
    ):
        evaluate_professional_observation(
            observation=obs, request=req, context=context, now=naive_time
        )

    # Resource type mismatch (passing facility obs to prof evaluator)
    fac_obs = _make_facility_obs(outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE)
    with pytest.raises(
        VerificationPolicyInputError,
        match="observation must have resource_type PROFESSIONAL",
    ):
        evaluate_professional_observation(
            observation=fac_obs, request=req, context=context, now=now
        )

    # Invalid version (< 1)
    with pytest.raises(VerificationPolicyInputError, match="current_version"):
        ProfessionalVerificationContext(
            current_status=ProfessionalVerificationStatus.VERIFIED,
            current_version=0,
            registration_authority_code="TEST_PROF_AUTH",
            registration_number_normalized="REG1001",
        )

    # Error code format
    err = VerificationPolicyInputError("safe failure message")
    assert str(err) == "[VERIFICATION_POLICY_INPUT_INVALID] safe failure message"
    assert issubclass(VerificationPolicyInputError, VerificationPolicyError)


# ---------------------------------------------------------------------------
# AST Architectural Isolation Guard
# ---------------------------------------------------------------------------


def test_ast_architectural_isolation() -> None:
    """AST analysis verifies that provider_verification_decision_policy has zero forbidden dependencies."""
    target_path = (
        Path(__file__).parent.parent
        / "app"
        / "services"
        / "provider_verification_decision_policy.py"
    )
    assert target_path.is_file()

    tree = ast.parse(target_path.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module.split(".")[0])

    forbidden_modules = {
        # Networking / HTTP
        "httpx",
        "requests",
        "urllib",
        "aiohttp",
        "socket",
        "http",
        # Database / ORM
        "sqlalchemy",
        "sqlmodel",
        "psycopg2",
        "asyncpg",
        "alembic",
        # Caching
        "redis",
        "aioredis",
        # Web / Framework
        "fastapi",
        "starlette",
    }

    violation = imported_modules & forbidden_modules
    assert not violation, f"Forbidden modules imported in provider_verification_decision_policy.py: {violation}"


# ---------------------------------------------------------------------------
# Shadow-Vocabulary, Canonical Enum, and Provenance Gap Inspection Tests
# ---------------------------------------------------------------------------


def test_static_shadow_vocabulary_inspection() -> None:
    """Inspect module text and AST for forbidden shadow lifecycle vocabulary."""
    target_path = (
        Path(__file__).parent.parent
        / "app"
        / "services"
        / "provider_verification_decision_policy.py"
    )
    assert target_path.is_file()
    text = target_path.read_text(encoding="utf-8")

    forbidden_shadow_names = [
        "REJECTED_INITIAL",
        "CLOSED_TERMINATED",
        "RESTORE_SUSPENDED",
        "CONFIRM_INITIAL_REVIEW",
        "REJECT_INITIAL",
        "SET_FACILITY_VERIFIED",
        "SET_FACILITY_SUSPENDED",
        "SET_PROFESSIONAL_VERIFIED",
        "SET_PROFESSIONAL_SUSPENDED",
        "REVOKED_TERMINATED",
        "EXPIRED_TERMINATED",
        "STALE_TERMINATED",
        "FACILITY_ACTIVE",
        "FACILITY_INACTIVE",
        "CLOSE_TERMINATED",
    ]
    for name in forbidden_shadow_names:
        assert (
            name not in text
        ), f"Forbidden shadow name '{name}' found in decision policy module!"


def test_canonical_lifecycle_vocabularies() -> None:
    """Verify that all state and command vocabularies strictly match canonical enums."""
    expected_prof_statuses = {
        "NOT_SUBMITTED",
        "PENDING_REVIEW",
        "VERIFIED",
        "RECHECK_DUE",
        "VERIFICATION_STALE",
        "SUSPENDED",
        "REJECTED",
        "REVOKED",
        "EXPIRED",
    }
    assert {s.value for s in ProfessionalVerificationStatus} == expected_prof_statuses

    expected_facility_statuses = {
        "DRAFT",
        "PENDING_VERIFICATION",
        "VERIFIED",
        "RECHECK_REQUIRED",
        "SUSPENDED",
        "REJECTED",
        "CLOSED",
    }
    assert {s.value for s in FacilityVerificationStatus} == expected_facility_statuses

    expected_prof_commands = {
        "SUBMIT",
        "VERIFY",
        "REJECT",
        "SUSPEND",
        "RESTORE",
        "MARK_RECHECK_DUE",
        "COMPLETE_RECHECK",
        "MARK_STALE",
        "REVOKE",
        "EXPIRE",
    }
    assert {c.value for c in ProfessionalTransitionCommand} == expected_prof_commands

    expected_facility_commands = {
        "SUBMIT",
        "VERIFY",
        "REJECT",
        "SUSPEND",
        "RESTORE",
        "MARK_RECHECK_REQUIRED",
        "COMPLETE_RECHECK",
        "CLOSE",
    }
    assert {c.value for c in FacilityTransitionCommand} == expected_facility_commands

    expected_purposes = {
        "INITIAL_VERIFICATION",
        "RECHECK",
        "ADVERSE_SIGNAL_CHECK",
        "MANUAL_REVIEW",
    }
    assert {p.value for p in VerificationEvidenceLookupPurpose} == expected_purposes

    expected_bindings = {
        "NOT_EVALUATED",
        "MATCHED",
        "MISMATCHED",
        "AMBIGUOUS",
    }
    assert {b.value for b in VerificationIdentityBindingResult} == expected_bindings

    expected_outcomes = {
        "CONFIRMED_ACTIVE",
        "CONFIRMED_INACTIVE",
        "NOT_FOUND",
        "IDENTITY_MISMATCH",
        "AMBIGUOUS",
        "SOURCE_UNAVAILABLE",
        "SOURCE_RESPONSE_INVALID",
        "SOURCE_AUTHENTICATION_FAILURE",
        "SOURCE_INTEGRITY_FAILURE",
        "REVIEW_REQUIRED",
    }
    assert {o.value for o in VerificationEvidenceOutcome} == expected_outcomes


def test_context_constructors_reject_string_or_shadow_states() -> None:
    """Context constructors reject arbitrary state strings or shadow names."""
    with pytest.raises(
        VerificationPolicyInputError,
        match="current_status must be a ProfessionalVerificationStatus",
    ):
        ProfessionalVerificationContext(
            current_status="VERIFIED",  # type: ignore[arg-type]
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )

    with pytest.raises(
        VerificationPolicyInputError,
        match="current_status must be a ProfessionalVerificationStatus",
    ):
        ProfessionalVerificationContext(
            current_status="REJECTED_INITIAL",  # type: ignore[arg-type]
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )

    with pytest.raises(
        VerificationPolicyInputError,
        match="current_status must be a FacilityVerificationStatus",
    ):
        FacilityVerificationContext(
            current_status="VERIFIED",  # type: ignore[arg-type]
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )

    with pytest.raises(
        VerificationPolicyInputError,
        match="current_status must be a FacilityVerificationStatus",
    ):
        FacilityVerificationContext(
            current_status="CLOSED_TERMINATED",  # type: ignore[arg-type]
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )


def test_decision_plan_candidate_command_type_soundness() -> None:
    """Enumerate every candidate command emission and verify it uses canonical command enums."""
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Professional COMPLETE_RECHECK
    prof_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    prof_ctx_recheck = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE",
    )
    prof_obs_active = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        source_id="TEST_SOURCE",
    )
    plan1 = evaluate_professional_observation(
        observation=prof_obs_active, request=prof_req, context=prof_ctx_recheck, now=now
    )
    assert (
        plan1.disposition == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
    )
    assert plan1.candidate_command is ProfessionalTransitionCommand.COMPLETE_RECHECK
    assert isinstance(plan1.candidate_command, ProfessionalTransitionCommand)

    # 2. Professional MARK_RECHECK_DUE (outage with grace)
    prof_ctx_verified = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE",
        registration_valid_until=now + timedelta(days=30),
        previous_verification_valid=True,
    )
    prof_obs_outage = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        source_id="TEST_SOURCE",
    )
    plan2 = evaluate_professional_observation(
        observation=prof_obs_outage,
        request=prof_req,
        context=prof_ctx_verified,
        now=now,
    )
    assert (
        plan2.disposition == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
    )
    assert plan2.candidate_command is ProfessionalTransitionCommand.MARK_RECHECK_DUE
    assert isinstance(plan2.candidate_command, ProfessionalTransitionCommand)

    # 3. Professional MARK_RECHECK_DUE (non-outage fail-closed review)
    prof_obs_inval = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        source_id="TEST_SOURCE",
    )
    plan3 = evaluate_professional_observation(
        observation=prof_obs_inval, request=prof_req, context=prof_ctx_verified, now=now
    )
    assert (
        plan3.disposition
        == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
    )
    assert plan3.candidate_command is ProfessionalTransitionCommand.MARK_RECHECK_DUE
    assert isinstance(plan3.candidate_command, ProfessionalTransitionCommand)

    # 4. Facility COMPLETE_RECHECK
    fac_req = FacilityLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    fac_ctx_recheck = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.RECHECK_REQUIRED,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE",
    )
    fac_obs_active = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        source_id="TEST_SOURCE",
    )
    plan4 = evaluate_facility_observation(
        observation=fac_obs_active, request=fac_req, context=fac_ctx_recheck, now=now
    )
    assert (
        plan4.disposition == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
    )
    assert plan4.candidate_command is FacilityTransitionCommand.COMPLETE_RECHECK
    assert isinstance(plan4.candidate_command, FacilityTransitionCommand)

    # 5. Facility MARK_RECHECK_REQUIRED (outage fail-closed without grace)
    fac_ctx_verified = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.VERIFIED,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE",
    )
    fac_obs_outage = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        source_id="TEST_SOURCE",
    )
    plan5 = evaluate_facility_observation(
        observation=fac_obs_outage, request=fac_req, context=fac_ctx_verified, now=now
    )
    assert (
        plan5.disposition
        == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
    )
    assert plan5.candidate_command is FacilityTransitionCommand.MARK_RECHECK_REQUIRED
    assert isinstance(plan5.candidate_command, FacilityTransitionCommand)

    # 6. Facility MARK_RECHECK_REQUIRED (non-outage fail-closed review)
    fac_obs_inval = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_RESPONSE_INVALID,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        source_id="TEST_SOURCE",
    )
    plan6 = evaluate_facility_observation(
        observation=fac_obs_inval, request=fac_req, context=fac_ctx_verified, now=now
    )
    assert (
        plan6.disposition
        == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
    )
    assert plan6.candidate_command is FacilityTransitionCommand.MARK_RECHECK_REQUIRED
    assert isinstance(plan6.candidate_command, FacilityTransitionCommand)


def test_terminal_state_matrix_exhaustive() -> None:
    """Every exact canonical terminal state denies automated candidate transition for all outcomes."""
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prof_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    prof_terminals = [
        ProfessionalVerificationStatus.REJECTED,
        ProfessionalVerificationStatus.REVOKED,
        ProfessionalVerificationStatus.EXPIRED,
        ProfessionalVerificationStatus.VERIFICATION_STALE,
    ]
    for status in prof_terminals:
        ctx = ProfessionalVerificationContext(
            current_status=status,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )
        for outcome in VerificationEvidenceOutcome:
            obs = _make_prof_obs(outcome=outcome)
            plan = evaluate_professional_observation(
                observation=obs, request=prof_req, context=ctx, now=now
            )
            assert plan.candidate_command is None
            assert (
                plan.disposition
                != VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
            )
            assert (
                plan.reason_code
                == VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION
            )

    fac_req = FacilityLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    fac_terminals = [
        FacilityVerificationStatus.REJECTED,
        FacilityVerificationStatus.CLOSED,
    ]
    for status in fac_terminals:
        ctx = FacilityVerificationContext(
            current_status=status,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )
        for outcome in VerificationEvidenceOutcome:
            obs = _make_facility_obs(outcome=outcome)
            plan = evaluate_facility_observation(
                observation=obs, request=fac_req, context=ctx, now=now
            )
            assert plan.candidate_command is None
            assert (
                plan.disposition
                != VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
            )
            assert (
                plan.reason_code
                == VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION
            )


def test_suspended_state_matrix_exhaustive() -> None:
    """Suspended states for professional and facility always remain HUMAN_REVIEW_REQUIRED across all outcomes."""
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prof_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    prof_ctx = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.SUSPENDED,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
    )
    for outcome in VerificationEvidenceOutcome:
        obs = _make_prof_obs(outcome=outcome)
        plan = evaluate_professional_observation(
            observation=obs, request=prof_req, context=prof_ctx, now=now
        )
        assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
        assert plan.candidate_command is None
        assert plan.requires_human_review is True
        assert (
            plan.reason_code == VerificationDecisionReason.SUSPENDED_STATE_NO_AUTOMATION
        )

    fac_req = FacilityLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    fac_ctx = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.SUSPENDED,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
    )
    for outcome in VerificationEvidenceOutcome:
        obs = _make_facility_obs(outcome=outcome)
        plan = evaluate_facility_observation(
            observation=obs, request=fac_req, context=fac_ctx, now=now
        )
        assert plan.disposition == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
        assert plan.candidate_command is None
        assert plan.requires_human_review is True
        assert (
            plan.reason_code == VerificationDecisionReason.SUSPENDED_STATE_NO_AUTOMATION
        )


def test_initial_state_matrix_exhaustive() -> None:
    """Canonical initial states never receive automated VERIFY even with CONFIRMED_ACTIVE + MATCHED."""
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prof_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    prof_initials = [
        ProfessionalVerificationStatus.NOT_SUBMITTED,
        ProfessionalVerificationStatus.PENDING_REVIEW,
    ]
    for status in prof_initials:
        ctx = ProfessionalVerificationContext(
            current_status=status,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )
        for outcome in VerificationEvidenceOutcome:
            obs = _make_prof_obs(
                outcome=outcome,
                purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            )
            plan = evaluate_professional_observation(
                observation=obs, request=prof_req, context=ctx, now=now
            )
            assert plan.candidate_command is None
            assert (
                plan.disposition
                == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
            )
            assert (
                plan.reason_code
                == VerificationDecisionReason.INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED
            )

    fac_req = FacilityLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    fac_initials = [
        FacilityVerificationStatus.DRAFT,
        FacilityVerificationStatus.PENDING_VERIFICATION,
    ]
    for status in fac_initials:
        ctx = FacilityVerificationContext(
            current_status=status,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
        )
        for outcome in VerificationEvidenceOutcome:
            obs = _make_facility_obs(
                outcome=outcome,
                purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
            )
            plan = evaluate_facility_observation(
                observation=obs, request=fac_req, context=ctx, now=now
            )
            assert plan.candidate_command is None
            assert (
                plan.disposition
                == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
            )
            assert (
                plan.reason_code
                == VerificationDecisionReason.INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED
            )


def test_system_actor_provenance_gap_constant() -> None:
    """Verify SYSTEM_ACTOR_PROVENANCE_GAP constant accurately describes the semantic gap without UUID claims."""
    assert isinstance(SYSTEM_ACTOR_PROVENANCE_GAP, str)
    assert len(SYSTEM_ACTOR_PROVENANCE_GAP) > 0
    assert "reviewer_id" in SYSTEM_ACTOR_PROVENANCE_GAP
    assert "String(128)" in SYSTEM_ACTOR_PROVENANCE_GAP
    assert "authenticated human provider actor" in SYSTEM_ACTOR_PROVENANCE_GAP
    assert "Phase 5E" in SYSTEM_ACTOR_PROVENANCE_GAP
    assert "requires a UUID" not in SYSTEM_ACTOR_PROVENANCE_GAP


# ---------------------------------------------------------------------------
# Phase 5D Post-Commit Correctness Tests: Failure Vocabulary, Purpose Binding & Gaps
# ---------------------------------------------------------------------------


def test_recheck_failure_reason_uses_verification_source_failure_reason() -> None:
    """Prove current_recheck_failure_reason uses VerificationSourceFailureReason and rejects observation outcomes."""
    expected_failure_reasons = {
        "SOURCE_UNAVAILABLE",
        "SOURCE_RESPONSE_INVALID",
        "SOURCE_NOT_FOUND",
        "REVIEW_REQUIRED",
    }
    assert {
        r.value for r in VerificationSourceFailureReason
    } == expected_failure_reasons

    # Valid failure reasons can populate context
    for reason in VerificationSourceFailureReason:
        prof_ctx = ProfessionalVerificationContext(
            current_status=ProfessionalVerificationStatus.VERIFIED,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
            current_recheck_failure_reason=reason,
        )
        assert prof_ctx.current_recheck_failure_reason is reason

        fac_ctx = FacilityVerificationContext(
            current_status=FacilityVerificationStatus.VERIFIED,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
            current_recheck_failure_reason=reason,
        )
        assert fac_ctx.current_recheck_failure_reason is reason

    # Observation-only outcomes must be rejected as lifecycle failure state
    invalid_outcomes = [
        VerificationEvidenceOutcome.SOURCE_AUTHENTICATION_FAILURE,
        VerificationEvidenceOutcome.SOURCE_INTEGRITY_FAILURE,
        VerificationEvidenceOutcome.CONFIRMED_INACTIVE,
        VerificationEvidenceOutcome.IDENTITY_MISMATCH,
    ]
    for inv in invalid_outcomes:
        with pytest.raises(
            VerificationPolicyInputError,
            match="current_recheck_failure_reason must be a VerificationSourceFailureReason",
        ):
            ProfessionalVerificationContext(
                current_status=ProfessionalVerificationStatus.VERIFIED,
                current_version=1,
                registration_authority_code="AUTH",
                registration_number_normalized="REG1",
                current_recheck_failure_reason=inv,  # type: ignore[arg-type]
            )

        with pytest.raises(
            VerificationPolicyInputError,
            match="current_recheck_failure_reason must be a VerificationSourceFailureReason",
        ):
            FacilityVerificationContext(
                current_status=FacilityVerificationStatus.VERIFIED,
                current_version=1,
                registration_authority_code="AUTH",
                registration_number_normalized="REG1",
                current_recheck_failure_reason=inv,  # type: ignore[arg-type]
            )


def test_request_observation_purpose_mismatch_fails_closed() -> None:
    """Request and observation lookup purpose mismatch fails closed with safe static error."""
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    prof_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    prof_obs_mismatch = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
    )
    prof_ctx = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.RECHECK_DUE,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
    )

    with pytest.raises(
        VerificationPolicyInputError,
        match="request and observation lookup purpose mismatch",
    ) as exc_prof:
        evaluate_professional_observation(
            observation=prof_obs_mismatch,
            request=prof_req,
            context=prof_ctx,
            now=now,
        )
    assert (
        str(exc_prof.value)
        == "[VERIFICATION_POLICY_INPUT_INVALID] request and observation lookup purpose mismatch"
    )

    fac_req = FacilityLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    fac_obs_mismatch = _make_facility_obs(
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
    )
    fac_ctx = FacilityVerificationContext(
        current_status=FacilityVerificationStatus.RECHECK_REQUIRED,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
    )

    with pytest.raises(
        VerificationPolicyInputError,
        match="request and observation lookup purpose mismatch",
    ) as exc_fac:
        evaluate_facility_observation(
            observation=fac_obs_mismatch,
            request=fac_req,
            context=fac_ctx,
            now=now,
        )
    assert (
        str(exc_fac.value)
        == "[VERIFICATION_POLICY_INPUT_INVALID] request and observation lookup purpose mismatch"
    )


def test_professional_grace_requires_recheck_purpose_and_adverse_outage_no_grace() -> (
    None
):
    """SOURCE_UNAVAILABLE yields grace ONLY on RECHECK; ADVERSE_SIGNAL_CHECK outage receives NO grace."""
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prof_ctx_verified = ProfessionalVerificationContext(
        current_status=ProfessionalVerificationStatus.VERIFIED,
        current_version=1,
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        server_provenance_established=True,
        established_server_source_id="TEST_SOURCE",
        registration_valid_until=now + timedelta(days=30),
        previous_verification_valid=True,
    )

    # 1. RECHECK purpose + SOURCE_UNAVAILABLE + prerequisites -> GRACE GRANTED
    recheck_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    recheck_obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        purpose=VerificationEvidenceLookupPurpose.RECHECK,
        source_id="TEST_SOURCE",
    )
    plan_grace = evaluate_professional_observation(
        observation=recheck_obs,
        request=recheck_req,
        context=prof_ctx_verified,
        now=now,
    )
    assert (
        plan_grace.disposition
        == VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE
    )
    assert (
        plan_grace.candidate_command is ProfessionalTransitionCommand.MARK_RECHECK_DUE
    )
    assert (
        plan_grace.reason_code
        == VerificationDecisionReason.SOURCE_UNAVAILABLE_BOUNDED_GRACE
    )
    assert plan_grace.grace_expires_at == now + timedelta(hours=24)
    assert plan_grace.requires_human_review is False

    # 2. ADVERSE_SIGNAL_CHECK purpose + SOURCE_UNAVAILABLE -> NO GRACE (fail-closed and review)
    adverse_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
    )
    adverse_obs = _make_prof_obs(
        outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
        purpose=VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK,
        source_id="TEST_SOURCE",
    )
    plan_no_grace = evaluate_professional_observation(
        observation=adverse_obs,
        request=adverse_req,
        context=prof_ctx_verified,
        now=now,
    )
    assert (
        plan_no_grace.disposition
        == VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW
    )
    assert (
        plan_no_grace.candidate_command
        is ProfessionalTransitionCommand.MARK_RECHECK_DUE
    )
    assert (
        plan_no_grace.reason_code
        == VerificationDecisionReason.SOURCE_UNAVAILABLE_NO_GRACE_FAIL_CLOSED
    )
    assert plan_no_grace.grace_expires_at is None
    assert plan_no_grace.requires_human_review is True


def test_manual_review_purpose_always_human_gated() -> None:
    """MANUAL_REVIEW lookup purpose never produces system automation candidates across all states and outcomes.

    Professional: 9 statuses x 10 outcomes = 90 combinations
    Facility: 7 statuses x 10 outcomes = 70 combinations
    Total: 160 combinations
    """
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    prof_req = ProfessionalLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW,
    )

    prof_combinations = 0
    for status in ProfessionalVerificationStatus:
        prof_ctx = ProfessionalVerificationContext(
            current_status=status,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
            server_provenance_established=True,
            established_server_source_id="TEST_SOURCE",
        )
        for outcome in VerificationEvidenceOutcome:
            obs = _make_prof_obs(
                outcome=outcome,
                purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW,
                source_id="TEST_SOURCE",
            )
            plan = evaluate_professional_observation(
                observation=obs,
                request=prof_req,
                context=prof_ctx,
                now=now,
            )
            assert (
                plan.disposition
                == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
            )
            assert plan.candidate_command is None
            assert plan.requires_human_review is True
            assert plan.grace_expires_at is None
            assert (
                plan.reason_code
                == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED
            )
            prof_combinations += 1
    assert prof_combinations == 90

    fac_req = FacilityLookupRequest(
        registration_authority_code="AUTH",
        registration_number_normalized="REG1",
        lookup_purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW,
    )

    fac_combinations = 0
    for status in FacilityVerificationStatus:
        fac_ctx = FacilityVerificationContext(
            current_status=status,
            current_version=1,
            registration_authority_code="AUTH",
            registration_number_normalized="REG1",
            server_provenance_established=True,
            established_server_source_id="TEST_SOURCE",
        )
        for outcome in VerificationEvidenceOutcome:
            obs = _make_facility_obs(
                outcome=outcome,
                purpose=VerificationEvidenceLookupPurpose.MANUAL_REVIEW,
                source_id="TEST_SOURCE",
            )
            plan = evaluate_facility_observation(
                observation=obs,
                request=fac_req,
                context=fac_ctx,
                now=now,
            )
            assert (
                plan.disposition
                == VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED
            )
            assert plan.candidate_command is None
            assert plan.requires_human_review is True
            assert plan.grace_expires_at is None
            assert (
                plan.reason_code
                == VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED
            )
            fac_combinations += 1
    assert fac_combinations == 70
    assert prof_combinations + fac_combinations == 160


def test_observation_request_binding_gap_constant() -> None:
    """Verify OBSERVATION_REQUEST_BINDING_GAP constant accurately records the structural pairing gap."""
    assert isinstance(OBSERVATION_REQUEST_BINDING_GAP, str)
    assert len(OBSERVATION_REQUEST_BINDING_GAP) > 0
    assert "RegistryObservation" in OBSERVATION_REQUEST_BINDING_GAP
    assert "registration authority code" in OBSERVATION_REQUEST_BINDING_GAP
    assert "Phase 5E" in OBSERVATION_REQUEST_BINDING_GAP
    assert "structural invocation lineage" in OBSERVATION_REQUEST_BINDING_GAP
