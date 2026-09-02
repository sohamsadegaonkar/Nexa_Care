from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    VerificationSourceFailureReason,
)
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    ClinicalEligibilityUnavailable,
    ContactAssurancePolicy,
    DelegatedInitiationAssurance,
    InteractiveClinicalAuthentication,
    MAX_DELEGATED_TRUST_STALENESS,
)


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _TrustDb:
    def __init__(self, provider: ProviderIdentity, hospital: HospitalRegistry) -> None:
        self._values = [provider, hospital]

    async def execute(self, _statement: object) -> _Result:
        return _Result(self._values.pop(0))


class _UnavailableTrustDb:
    async def execute(self, _statement: object) -> _Result:
        raise OSError("synthetic trust-store outage")


def _run(awaitable):
    return asyncio.run(awaitable)


def _trusted_rows(now: datetime):
    provider = ProviderIdentity(
        id=uuid4(),
        status="active",
        is_active=True,
        email_verified_at=now - timedelta(days=1),
        phone_verified_at=now - timedelta(days=1),
    )
    provider.credential = ProviderCredential(
        provider_id=provider.id,
        login_identifier="clinician@example.test",
        password_hash="not-used-by-these-tests",
        is_active=True,
        mfa_enabled=True,
    )
    provider.professional_verification = ProfessionalVerification(
        provider_id=provider.id,
        status=ProfessionalVerificationStatus.VERIFIED.value,
        verified_at=now - timedelta(days=1),
        registration_valid_until=now + timedelta(days=10),
        next_review_at=now + timedelta(days=5),
    )
    hospital = HospitalRegistry(id=uuid4(), is_active=True)
    hospital.verification = FacilityVerification(
        facility_id=hospital.id,
        status=FacilityVerificationStatus.VERIFIED.value,
        verified_at=now - timedelta(days=1),
        next_review_at=now + timedelta(days=4),
    )
    affiliation = ProviderHospitalAffiliation(
        id=uuid4(),
        provider_id=provider.id,
        hospital_id=hospital.id,
        roles=["clinician"],
        trust_status=AffiliationTrustStatus.ACTIVE.value,
        valid_until=now + timedelta(days=3),
    )
    provider.affiliations = [affiliation]
    return provider, hospital, affiliation


def _service() -> ClinicalEligibilityService:
    return ClinicalEligibilityService(
        contact_assurance_policy=ContactAssurancePolicy(
            require_email_verified=True,
            require_phone_verified=False,
            version="test-contact-policy-v1",
        ),
        recent_mfa_max_age_seconds=lambda: 600,
    )


def _interactive(provider: ProviderIdentity, hospital: HospitalRegistry, now: datetime):
    return InteractiveClinicalAuthentication(
        provider_id=provider.id,
        hospital_id=hospital.id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=now - timedelta(seconds=10),
    )


def test_interactive_requires_all_independent_trust_facts() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, affiliation = _trusted_rows(now)
    result = _run(
        _service().evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            _interactive(provider, hospital, now),
            ClinicalCapability.RECORD_READ,
            now=now,
        )
    )

    assert result.allowed is True
    assert result.affiliation_id == affiliation.id
    assert result.denial_code is None
    assert result.decision_valid_until is None


def test_basic_auth_and_mfa_enrollment_alone_do_not_qualify() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    authentication = InteractiveClinicalAuthentication(
        provider_id=provider.id,
        hospital_id=hospital.id,
        method=ClinicalAuthenticationMethod.BASIC,
        session_authenticated=True,
        mfa_verified_at=now,
    )
    result = _run(
        _service().evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            authentication,
            ClinicalCapability.RECORD_READ,
            now=now,
        )
    )
    assert result.denial_code is ClinicalEligibilityDenialCode.CLINICAL_SESSION_REQUIRED


def test_undefined_contact_policy_fails_closed_before_professional_trust() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    result = _run(
        ClinicalEligibilityService().evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            _interactive(provider, hospital, now),
            ClinicalCapability.RECORD_READ,
            now=now,
        )
    )
    assert (
        result.denial_code
        is ClinicalEligibilityDenialCode.CONTACT_ASSURANCE_POLICY_UNDEFINED
    )


def test_recheck_grace_requires_exact_source_unavailable_evidence() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    verification = provider.professional_verification
    verification.status = ProfessionalVerificationStatus.RECHECK_DUE.value
    verification.previous_verification_valid = True
    verification.recheck_attempted_at = now - timedelta(minutes=1)
    verification.recheck_failure_reason = (
        VerificationSourceFailureReason.SOURCE_UNAVAILABLE.value
    )
    verification.grace_expires_at = now + timedelta(minutes=2)
    allowed = _run(
        _service().evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            _interactive(provider, hospital, now),
            ClinicalCapability.RECORD_READ,
            now=now,
        )
    )
    assert allowed.allowed is True
    assert allowed.professional_grace_active is True

    verification.recheck_failure_reason = "OTHER_FAILURE"
    denied = _run(
        _service().evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            _interactive(provider, hospital, now),
            ClinicalCapability.RECORD_READ,
            now=now,
        )
    )
    assert (
        denied.denial_code
        is ClinicalEligibilityDenialCode.PROFESSIONAL_RECHECK_NOT_ELIGIBLE
    )


def test_delegated_result_is_bounded_and_rechecks_current_suspension() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    assurance = DelegatedInitiationAssurance(
        initiated_by_provider_id=provider.id,
        initiated_hospital_id=hospital.id,
        initiated_at=now - timedelta(seconds=5),
        authentication_method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        mfa_verified_at=now - timedelta(seconds=6),
        assurance_policy_version="test-contact-policy-v1",
        workflow_id=uuid4(),
        consent_request_id=uuid4(),
        required_capability=ClinicalCapability.DOCUMENTS_PROCESS,
        workflow_authorization_current=True,
    )
    service = _service()
    result = _run(
        service.evaluate_delegated(
            _TrustDb(provider, hospital),
            provider.id,
            hospital.id,
            assurance,
            ClinicalCapability.DOCUMENTS_PROCESS,
            now=now,
        )
    )
    assert result.allowed is True
    assert result.decision_valid_until == now + MAX_DELEGATED_TRUST_STALENESS
    assert service.decision_is_current(result, now=now + timedelta(seconds=59))
    assert not service.decision_is_current(result, now=now + timedelta(seconds=60))

    provider.professional_verification.status = (
        ProfessionalVerificationStatus.SUSPENDED.value
    )
    suspended = _run(
        service.evaluate_delegated(
            _TrustDb(provider, hospital),
            provider.id,
            hospital.id,
            assurance,
            ClinicalCapability.DOCUMENTS_PROCESS,
            now=now + timedelta(seconds=1),
        )
    )
    assert suspended.denial_code is ClinicalEligibilityDenialCode.PROFESSIONAL_SUSPENDED


def test_delegated_policy_version_drift_fails_closed() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    assurance = DelegatedInitiationAssurance(
        initiated_by_provider_id=provider.id,
        initiated_hospital_id=hospital.id,
        initiated_at=now - timedelta(seconds=5),
        authentication_method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        mfa_verified_at=now - timedelta(seconds=6),
        assurance_policy_version="retired-policy-version",
        workflow_id=uuid4(),
        consent_request_id=uuid4(),
        required_capability=ClinicalCapability.DOCUMENTS_PROCESS,
        workflow_authorization_current=True,
    )
    result = _run(
        _service().evaluate_delegated(
            _TrustDb(provider, hospital),
            provider.id,
            hospital.id,
            assurance,
            ClinicalCapability.DOCUMENTS_PROCESS,
            now=now,
        )
    )
    assert (
        result.denial_code
        is ClinicalEligibilityDenialCode.DELEGATED_INITIATION_ASSURANCE_INVALID
    )


def test_legacy_clinician_role_and_registration_claim_do_not_create_trust() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    provider.medical_registration_number = "legacy-claim"
    provider.professional_verification.status = (
        ProfessionalVerificationStatus.NOT_SUBMITTED.value
    )
    result = _run(
        _service().evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            _interactive(provider, hospital, now),
            ClinicalCapability.RECORD_READ,
            now=now,
        )
    )
    assert (
        result.denial_code
        is ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_REQUIRED
    )


def test_trust_store_outage_raises_dedicated_unavailable_error() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    try:
        _run(
            _service().evaluate_interactive(
                _UnavailableTrustDb(),
                provider,
                _interactive(provider, hospital, now),
                ClinicalCapability.RECORD_READ,
                now=now,
            )
        )
    except ClinicalEligibilityUnavailable:
        pass
    else:
        raise AssertionError("trust-store outage must not become an ordinary denial")


def test_timezone_naive_trust_evidence_fails_closed() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    provider, hospital, _ = _trusted_rows(now)
    provider.professional_verification.verified_at = datetime(2026, 8, 29)
    result = _run(
        _service().evaluate_interactive(
            _TrustDb(provider, hospital),
            provider,
            _interactive(provider, hospital, now),
            ClinicalCapability.RECORD_READ,
            now=now,
        )
    )
    assert (
        result.denial_code
        is ClinicalEligibilityDenialCode.TRUST_STATE_INTEGRITY_FAILURE
    )
