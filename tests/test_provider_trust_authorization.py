from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

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
    ProviderTrustPermissionGrant,
)
from app.security.provider_capabilities import ClinicalCapability, capability_is_granted
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
    scope_for_permission,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.provider_trust_authorization import (
    ProviderTrustAuthorizationService,
    TrustAuthorizationDenialCode,
    TrustManagementAuthentication,
    affiliation_command_permission,
    facility_command_permission,
    professional_command_permission,
)
from app.services.provider_trust_lifecycle import (
    AffiliationTransitionCommand,
    FacilityTransitionCommand,
    ProfessionalTransitionCommand,
)


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Result:
    def __init__(self, value, many=False):
        self.value, self.many = value, many

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return _Scalars(self.value if self.many else [])


class _Db:
    def __init__(self, values):
        self.values = list(values)

    async def execute(self, _statement):
        value = self.values.pop(0)
        return _Result(value, isinstance(value, list))


def _actor(*, mfa=True, email=True, phone=True):
    actor = ProviderIdentity(id=uuid4(), status="active", is_active=True)
    actor.email_verified_at = NOW if email else None
    actor.phone_verified_at = NOW if phone else None
    actor.credential = ProviderCredential(
        provider_id=actor.id,
        login_identifier=f"{actor.id}@example.test",
        password_hash="test",
        is_active=True,
        mfa_enabled=mfa,
    )
    return actor


def _auth(actor, *, method=ClinicalAuthenticationMethod.PROVIDER_SESSION, mfa=True):
    return TrustManagementAuthentication(
        actor.id, method, True, NOW - timedelta(seconds=1) if mfa else None
    )


def _grant(actor, permission, facility_id=None, *, revoked=False):
    return ProviderTrustPermissionGrant(
        provider_id=actor.id,
        permission=permission.value,
        scope_type=scope_for_permission(permission).value,
        facility_id=facility_id,
        granted_by_actor_id="governance",
        revoked_at=NOW if revoked else None,
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_permission_vocabulary_is_closed_and_nonclinical():
    assert {item.value for item in TrustManagementPermission} == {
        "PROFESSIONAL_REVIEW",
        "FACILITY_REVIEW",
        "AFFILIATION_MANAGE",
        "TRUST_PERMISSION_MANAGE",
    }
    assert all(
        item.value not in {cap.value for cap in ClinicalCapability}
        for item in TrustManagementPermission
    )
    assert scope_for_permission(TrustManagementPermission.PROFESSIONAL_REVIEW) is (
        TrustPermissionScope.GLOBAL
    )
    assert scope_for_permission(TrustManagementPermission.FACILITY_REVIEW) is (
        TrustPermissionScope.FACILITY
    )
    with pytest.raises(TypeError):
        scope_for_permission("PROFESSIONAL_REVIEW")  # type: ignore[arg-type]


def test_phase_3c_command_mapping_is_server_owned_and_exhaustive():
    assert professional_command_permission(ProfessionalTransitionCommand.SUBMIT) is None
    assert all(
        professional_command_permission(command)
        is TrustManagementPermission.PROFESSIONAL_REVIEW
        for command in ProfessionalTransitionCommand
        if command is not ProfessionalTransitionCommand.SUBMIT
    )
    assert all(
        facility_command_permission(command)
        is TrustManagementPermission.FACILITY_REVIEW
        for command in FacilityTransitionCommand
    )
    assert all(
        affiliation_command_permission(command)
        is TrustManagementPermission.AFFILIATION_MANAGE
        for command in AffiliationTransitionCommand
    )
    with pytest.raises(TypeError):
        professional_command_permission("VERIFY")  # type: ignore[arg-type]


def test_legacy_roles_and_clinical_capability_do_not_authorize_review():
    for role in (
        "admin",
        "privacy_officer",
        "auditor",
        "clinical_reviewer",
        "clinician",
        "receptionist",
    ):
        actor = _actor()
        actor.role = role
        affiliation = ProviderHospitalAffiliation(
            id=uuid4(), provider_id=actor.id, hospital_id=uuid4(), roles=[role]
        )
        decision = _run(
            ProviderTrustAuthorizationService().authorize_professional_review(
                _Db([actor, []]),
                actor_id=actor.id,
                target_provider_id=uuid4(),
                authentication=_auth(actor),
                now=NOW,
            )
        )
        assert (
            decision.denial_code
            is TrustAuthorizationDenialCode.TRUST_PERMISSION_REQUIRED
        )
        assert capability_is_granted([role], ClinicalCapability.RECORD_READ) is (
            role == "clinician"
        )
        assert affiliation.roles == [role]


def test_clinical_and_lifecycle_state_do_not_substitute_for_a_trust_grant():
    actor = _actor()
    facility = HospitalRegistry(id=uuid4(), facility_code="SYNTHETIC")
    facility.verification = FacilityVerification(
        facility_id=facility.id,
        status=FacilityVerificationStatus.VERIFIED.value,
    )
    actor.professional_verification = ProfessionalVerification(
        provider_id=actor.id,
        status=ProfessionalVerificationStatus.VERIFIED.value,
    )
    actor.affiliations = [
        ProviderHospitalAffiliation(
            provider_id=actor.id,
            hospital_id=facility.id,
            roles=["clinician"],
            trust_status=AffiliationTrustStatus.ACTIVE.value,
        )
    ]
    decision = _run(
        ProviderTrustAuthorizationService().authorize_professional_review(
            _Db([actor, []]),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        decision.denial_code is TrustAuthorizationDenialCode.TRUST_PERMISSION_REQUIRED
    )


def test_global_review_requires_active_explicit_grant_and_forbids_self_review():
    actor = _actor()
    service = ProviderTrustAuthorizationService()
    allowed = _run(
        service.authorize_professional_review(
            _Db(
                [actor, [_grant(actor, TrustManagementPermission.PROFESSIONAL_REVIEW)]]
            ),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert allowed.allowed
    self_review = _run(
        service.authorize_professional_review(
            _Db([]),
            actor_id=actor.id,
            target_provider_id=actor.id,
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        self_review.denial_code is TrustAuthorizationDenialCode.SELF_REVIEW_PROHIBITED
    )
    revoked = _run(
        service.authorize_professional_review(
            _Db(
                [
                    actor,
                    [
                        _grant(
                            actor,
                            TrustManagementPermission.PROFESSIONAL_REVIEW,
                            revoked=True,
                        )
                    ],
                ]
            ),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        revoked.denial_code
        is TrustAuthorizationDenialCode.TRUST_PERMISSION_REVOKED_OR_INACTIVE
    )


def test_inactive_and_malformed_grants_fail_closed():
    actor = _actor()
    service = ProviderTrustAuthorizationService()
    expired = _grant(actor, TrustManagementPermission.PROFESSIONAL_REVIEW)
    expired.valid_until = NOW
    expired_decision = _run(
        service.authorize_professional_review(
            _Db([actor, [expired]]),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        expired_decision.denial_code
        is TrustAuthorizationDenialCode.TRUST_PERMISSION_REVOKED_OR_INACTIVE
    )
    malformed = _grant(actor, TrustManagementPermission.PROFESSIONAL_REVIEW)
    malformed.scope_type = TrustPermissionScope.FACILITY.value
    malformed.facility_id = uuid4()
    malformed_decision = _run(
        service.authorize_professional_review(
            _Db([actor, [malformed]]),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        malformed_decision.denial_code
        is TrustAuthorizationDenialCode.TRUST_PERMISSION_STATE_INVALID
    )
    unknown = _grant(actor, TrustManagementPermission.PROFESSIONAL_REVIEW)
    unknown.permission = "FORGED_PERMISSION"
    unknown_decision = _run(
        service.authorize_professional_review(
            _Db([actor, [unknown]]),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        unknown_decision.denial_code
        is TrustAuthorizationDenialCode.TRUST_PERMISSION_STATE_INVALID
    )
    future = _grant(actor, TrustManagementPermission.PROFESSIONAL_REVIEW)
    future.valid_from = NOW + timedelta(seconds=1)
    future_decision = _run(
        service.authorize_professional_review(
            _Db([actor, [future]]),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        future_decision.denial_code
        is TrustAuthorizationDenialCode.TRUST_PERMISSION_REVOKED_OR_INACTIVE
    )


def test_strong_auth_and_self_submission_are_independent_of_clinical_eligibility():
    actor = _actor()
    service = ProviderTrustAuthorizationService()
    for auth in (
        _auth(actor, method=ClinicalAuthenticationMethod.BASIC),
        _auth(actor, mfa=False),
    ):
        decision = _run(
            service.authorize_professional_self_submission(
                _Db([actor]),
                actor_id=actor.id,
                target_provider_id=actor.id,
                authentication=auth,
                now=NOW,
            )
        )
        assert decision.allowed is False
    allowed = _run(
        service.authorize_professional_self_submission(
            _Db([actor]),
            actor_id=actor.id,
            target_provider_id=actor.id,
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert allowed.allowed and allowed.permission is None


@pytest.mark.parametrize(
    ("mutate", "expected"),
    (
        (lambda actor: setattr(actor, "is_active", False), "ACCOUNT_INACTIVE"),
        (
            lambda actor: setattr(actor.credential, "is_active", False),
            "CREDENTIAL_INACTIVE",
        ),
        (
            lambda actor: setattr(actor, "email_verified_at", None),
            "CONTACT_VERIFICATION_REQUIRED",
        ),
        (
            lambda actor: setattr(actor, "phone_verified_at", None),
            "CONTACT_VERIFICATION_REQUIRED",
        ),
        (lambda actor: setattr(actor.credential, "mfa_enabled", False), "MFA_REQUIRED"),
    ),
)
def test_strong_authentication_denials_are_stable(mutate, expected):
    actor = _actor()
    mutate(actor)
    decision = _run(
        ProviderTrustAuthorizationService().authorize_professional_review(
            _Db([actor]),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert decision.denial_code.value == expected


def test_forged_permission_claim_and_unassured_session_do_not_authorize():
    actor = _actor()
    grant = _grant(actor, TrustManagementPermission.PROFESSIONAL_REVIEW)
    with pytest.raises(TypeError):
        TrustManagementAuthentication(  # type: ignore[call-arg]
            actor.id,
            ClinicalAuthenticationMethod.PROVIDER_SESSION,
            True,
            NOW,
            permission="PROFESSIONAL_REVIEW",
        )
    decision = _run(
        ProviderTrustAuthorizationService().authorize_professional_review(
            _Db([actor, [grant]]),
            actor_id=actor.id,
            target_provider_id=uuid4(),
            authentication=_auth(actor, mfa=False),
            now=NOW,
        )
    )
    assert (
        decision.denial_code
        is TrustAuthorizationDenialCode.MFA_SESSION_ASSURANCE_REQUIRED
    )


def test_facility_and_affiliation_permissions_are_exactly_facility_scoped():
    actor, facility_a, facility_b = _actor(), uuid4(), uuid4()
    service = ProviderTrustAuthorizationService()
    grant = _grant(actor, TrustManagementPermission.FACILITY_REVIEW, facility_a)
    allowed = _run(
        service.authorize_facility_review(
            _Db([actor, [grant]]),
            actor_id=actor.id,
            target_facility_id=facility_a,
            authentication=_auth(actor),
            now=NOW,
        )
    )
    denied = _run(
        service.authorize_facility_review(
            _Db([actor, [grant]]),
            actor_id=actor.id,
            target_facility_id=facility_b,
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert (
        allowed.allowed
        and denied.denial_code
        is TrustAuthorizationDenialCode.TRUST_PERMISSION_SCOPE_MISMATCH
    )
    target = ProviderHospitalAffiliation(
        id=uuid4(), provider_id=uuid4(), hospital_id=facility_a
    )
    affiliation_grant = _grant(
        actor, TrustManagementPermission.AFFILIATION_MANAGE, facility_a
    )
    assert _run(
        service.authorize_affiliation_management(
            _Db([target, actor, [affiliation_grant]]),
            actor_id=actor.id,
            target_affiliation_id=target.id,
            authentication=_auth(actor),
            now=NOW,
        )
    ).allowed
    own = ProviderHospitalAffiliation(
        id=uuid4(), provider_id=actor.id, hospital_id=facility_a
    )
    assert (
        _run(
            service.authorize_affiliation_management(
                _Db([own]),
                actor_id=actor.id,
                target_affiliation_id=own.id,
                authentication=_auth(actor),
                now=NOW,
            )
        ).denial_code
        is TrustAuthorizationDenialCode.SELF_AFFILIATION_MANAGEMENT_PROHIBITED
    )
    cross_facility = ProviderHospitalAffiliation(
        id=uuid4(), provider_id=uuid4(), hospital_id=facility_b
    )
    assert (
        _run(
            service.authorize_affiliation_management(
                _Db([cross_facility, actor, [affiliation_grant]]),
                actor_id=actor.id,
                target_affiliation_id=cross_facility.id,
                authentication=_auth(actor),
                now=NOW,
            )
        ).denial_code
        is TrustAuthorizationDenialCode.TRUST_PERMISSION_SCOPE_MISMATCH
    )


def test_trust_permission_management_requires_explicit_global_grant():
    actor = _actor()
    service = ProviderTrustAuthorizationService()
    denied = _run(
        service.authorize_trust_permission_management(
            _Db([actor, []]), actor_id=actor.id, authentication=_auth(actor), now=NOW
        )
    )
    assert denied.denial_code is TrustAuthorizationDenialCode.TRUST_PERMISSION_REQUIRED
    allowed = _run(
        service.authorize_trust_permission_management(
            _Db(
                [
                    actor,
                    [_grant(actor, TrustManagementPermission.TRUST_PERMISSION_MANAGE)],
                ]
            ),
            actor_id=actor.id,
            authentication=_auth(actor),
            now=NOW,
        )
    )
    assert allowed.allowed and allowed.scope is TrustPermissionScope.GLOBAL


def test_phase_3d_has_no_lifecycle_mutation_or_privileged_routes():
    root = Path(__file__).resolve().parents[1]
    service_source = (
        root / "app" / "services" / "provider_trust_authorization.py"
    ).read_text(encoding="utf-8")
    assert "db.commit" not in service_source
    assert "enqueue_audit_event" not in service_source
    assert "ClinicalEligibilityService" not in service_source
    assert "ClinicalCapability" not in service_source
    for route_source in (root / "app" / "api" / "v2").glob("*.py"):
        text = route_source.read_text(encoding="utf-8")
        assert "ProviderTrustAuthorizationService" not in text
        assert "ProviderTrustPermissionGrant" not in text
