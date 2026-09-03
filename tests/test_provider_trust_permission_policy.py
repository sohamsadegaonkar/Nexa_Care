"""Unit tests for the pure organizational trust permission policy module."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.audit_context import AuditDomain
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
import app.services.provider_trust_permission_policy as policy_module
from app.services.provider_trust_permission_policy import (
    ExistingGrantSlotFacts,
    GrantPlan,
    GrantRequestFacts,
    RevocationReasonCode,
    RevokePlan,
    RevokeRequestFacts,
    TargetProviderEligibilityFacts,
    TrustPermissionCommand,
    TrustPermissionPolicyError,
    plan_grant_permission,
    plan_revoke_permission,
)


def _active_target_eligibility() -> TargetProviderEligibilityFacts:
    return TargetProviderEligibilityFacts(
        provider_exists=True,
        account_is_active=True,
        account_status="active",
        credential_exists=True,
        credential_is_active=True,
    )


def test_grant_professional_review_produces_global_plan():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        facility_id=None,
        valid_from=now,
        valid_until=now + timedelta(days=365),
        governance_reference="REF-GOV-001",
        target_eligibility=_active_target_eligibility(),
    )

    plan = plan_grant_permission(facts, now=now)

    assert isinstance(plan, GrantPlan)
    assert plan.command is TrustPermissionCommand.GRANT
    assert plan.target_provider_id == target_id
    assert plan.permission is TrustManagementPermission.PROFESSIONAL_REVIEW
    assert plan.scope_type is TrustPermissionScope.GLOBAL
    assert plan.facility_id is None
    assert plan.governance_reference == "REF-GOV-001"
    assert plan.superseded_grant_id is None
    assert plan.superseded_revocation_reason is None
    assert plan.grant_event is ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_GRANTED
    assert plan.superseded_revoke_event is None


def test_grant_facility_review_produces_facility_plan():
    actor_id = uuid4()
    target_id = uuid4()
    facility_id = uuid4()
    now = datetime.now(timezone.utc)

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        facility_id=facility_id,
        target_eligibility=_active_target_eligibility(),
    )

    plan = plan_grant_permission(facts, now=now)

    assert plan.command is TrustPermissionCommand.GRANT
    assert plan.permission is TrustManagementPermission.FACILITY_REVIEW
    assert plan.scope_type is TrustPermissionScope.FACILITY
    assert plan.facility_id == facility_id


def test_grant_affiliation_manage_produces_facility_plan():
    actor_id = uuid4()
    target_id = uuid4()
    facility_id = uuid4()
    now = datetime.now(timezone.utc)

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.AFFILIATION_MANAGE,
        facility_id=facility_id,
        target_eligibility=_active_target_eligibility(),
    )

    plan = plan_grant_permission(facts, now=now)

    assert plan.command is TrustPermissionCommand.GRANT
    assert plan.permission is TrustManagementPermission.AFFILIATION_MANAGE
    assert plan.scope_type is TrustPermissionScope.FACILITY
    assert plan.facility_id == facility_id


def test_grant_root_permission_is_denied_offline_only():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE,
        facility_id=None,
        target_eligibility=_active_target_eligibility(),
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(facts, now=now)
    assert exc_info.value.code == "ROOT_PERMISSION_OFFLINE_ONLY"


def test_grant_self_grant_is_prohibited():
    actor_id = uuid4()
    now = datetime.now(timezone.utc)

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=actor_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        facility_id=None,
        target_eligibility=_active_target_eligibility(),
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(facts, now=now)
    assert exc_info.value.code == "SELF_GRANT_PROHIBITED"


def test_grant_denies_missing_or_inactive_target_provider():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    # Missing target
    f1 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=None,
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f1, now=now)
    assert exc_info.value.code == "TARGET_PROVIDER_NOT_FOUND"

    # Inactive target account
    f2 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=TargetProviderEligibilityFacts(
            provider_exists=True,
            account_is_active=False,
            account_status="inactive",
            credential_exists=True,
            credential_is_active=True,
        ),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f2, now=now)
    assert exc_info.value.code == "TARGET_PROVIDER_INACTIVE"

    # Inactive target credential
    f3 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=TargetProviderEligibilityFacts(
            provider_exists=True,
            account_is_active=True,
            account_status="active",
            credential_exists=True,
            credential_is_active=False,
        ),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f3, now=now)
    assert exc_info.value.code == "TARGET_CREDENTIAL_INACTIVE"


def test_grant_scope_mismatch_validation():
    actor_id = uuid4()
    target_id = uuid4()
    facility_id = uuid4()
    now = datetime.now(timezone.utc)

    # Global permission with facility_id
    f1 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        facility_id=facility_id,
        target_eligibility=_active_target_eligibility(),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f1, now=now)
    assert exc_info.value.code == "GLOBAL_PERMISSION_FACILITY_PROHIBITED"

    # Facility permission missing facility_id
    f2 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        facility_id=None,
        target_eligibility=_active_target_eligibility(),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f2, now=now)
    assert exc_info.value.code == "FACILITY_PERMISSION_FACILITY_REQUIRED"


def test_grant_temporal_validity_validation():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    # Naive datetime
    f1 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        valid_from=datetime(2026, 1, 1, 0, 0, 0),  # Naive
        target_eligibility=_active_target_eligibility(),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f1, now=now)
    assert exc_info.value.code == "INVALID_DATETIME_TIMEZONE"

    # valid_until <= valid_from
    f2 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        valid_from=now + timedelta(days=10),
        valid_until=now + timedelta(days=5),
        target_eligibility=_active_target_eligibility(),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f2, now=now)
    assert exc_info.value.code == "INVALID_VALIDITY_INTERVAL"


def test_grant_active_and_future_duplicate_slots_are_denied():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    # Active existing grant (no valid_until)
    existing_active = ExistingGrantSlotFacts(
        grant_id=uuid4(),
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )
    f1 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_active,
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f1, now=now)
    assert exc_info.value.code == "ACTIVE_GRANT_EXISTS"

    # Future-effective grant
    existing_future = ExistingGrantSlotFacts(
        grant_id=uuid4(),
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now + timedelta(days=5),
        valid_until=now + timedelta(days=30),
        revoked_at=None,
    )
    f2 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_future,
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f2, now=now)
    assert exc_info.value.code == "ACTIVE_GRANT_EXISTS"


def test_grant_expired_unrevoked_slot_produces_supersession_plan():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)
    old_grant_id = uuid4()

    # Expired unrevoked slot (valid_until in past, revoked_at is None)
    existing_expired = ExistingGrantSlotFacts(
        grant_id=old_grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=60),
        valid_until=now - timedelta(days=1),
        revoked_at=None,
    )

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        valid_from=now,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_expired,
    )

    plan = plan_grant_permission(facts, now=now)

    assert plan.command is TrustPermissionCommand.GRANT
    assert plan.target_provider_id == target_id
    assert plan.superseded_grant_id == old_grant_id
    assert plan.superseded_revocation_reason is RevocationReasonCode.EXPIRED_SUPERSEDED
    assert plan.grant_event is ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_GRANTED
    assert (
        plan.superseded_revoke_event
        is ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED
    )


def test_grant_revoked_historical_row_does_not_block_new_grant():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)
    old_grant_id = uuid4()

    # Already revoked grant
    existing_revoked = ExistingGrantSlotFacts(
        grant_id=old_grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=60),
        valid_until=None,
        revoked_at=now - timedelta(days=5),
    )

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_revoked,
    )

    plan = plan_grant_permission(facts, now=now)

    assert plan.command is TrustPermissionCommand.GRANT
    assert plan.target_provider_id == target_id
    assert plan.superseded_grant_id is None
    assert plan.superseded_revoke_event is None


# ---------------------------------------------------------------------------
# Mandatory Hardening Tests: Authoritative Grant State Validation (GRANT)
# ---------------------------------------------------------------------------


def test_grant_denies_existing_row_belonging_to_another_provider():
    actor_id = uuid4()
    target_id = uuid4()
    other_id = uuid4()
    now = datetime.now(timezone.utc)

    existing_mismatched_provider = ExistingGrantSlotFacts(
        grant_id=uuid4(),
        provider_id=other_id,  # Mismatched provider
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=now + timedelta(days=10),
        revoked_at=None,
    )

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_mismatched_provider,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_grant_denies_existing_row_with_different_permission():
    actor_id = uuid4()
    target_id = uuid4()
    facility_id = uuid4()
    now = datetime.now(timezone.utc)

    existing_diff_perm = ExistingGrantSlotFacts(
        grant_id=uuid4(),
        provider_id=target_id,
        permission=TrustManagementPermission.AFFILIATION_MANAGE,  # Mismatched permission
        scope_type=TrustPermissionScope.FACILITY,
        facility_id=facility_id,
        valid_from=now - timedelta(days=10),
        valid_until=now + timedelta(days=10),
        revoked_at=None,
    )

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        facility_id=facility_id,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_diff_perm,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_grant_denies_existing_row_with_wrong_facility():
    actor_id = uuid4()
    target_id = uuid4()
    requested_facility = uuid4()
    other_facility = uuid4()
    now = datetime.now(timezone.utc)

    existing_wrong_facility = ExistingGrantSlotFacts(
        grant_id=uuid4(),
        provider_id=target_id,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        scope_type=TrustPermissionScope.FACILITY,
        facility_id=other_facility,  # Mismatched facility
        valid_from=now - timedelta(days=10),
        valid_until=now + timedelta(days=10),
        revoked_at=None,
    )

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        facility_id=requested_facility,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_wrong_facility,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_grant_denies_malformed_scope_facility_binding():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    # Global permission with non-null facility_id on existing slot
    existing_malformed = ExistingGrantSlotFacts(
        grant_id=uuid4(),
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=uuid4(),  # Malformed: GLOBAL must have facility_id=None
        valid_from=now - timedelta(days=10),
        valid_until=now + timedelta(days=10),
        revoked_at=None,
    )

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        facility_id=None,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_malformed,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_grant_denies_naive_historical_validity_timestamp():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    # Naive timestamp on existing row
    existing_naive_ts = ExistingGrantSlotFacts(
        grant_id=uuid4(),
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=datetime(2025, 1, 1, 0, 0, 0),  # Naive
        valid_until=None,
        revoked_at=None,
    )

    facts = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        target_eligibility=_active_target_eligibility(),
        existing_slot_grant=existing_naive_ts,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


# ---------------------------------------------------------------------------
# Revoke Tests & Hardening Tests (REVOKE)
# ---------------------------------------------------------------------------


def test_revoke_subordinate_grant_succeeds():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
        governance_reference="TICKET-1234",
    )

    plan = plan_revoke_permission(facts, now=now)

    assert isinstance(plan, RevokePlan)
    assert plan.command is TrustPermissionCommand.REVOKE
    assert plan.target_grant_id == grant_id
    assert plan.target_provider_id == target_id
    assert plan.permission is TrustManagementPermission.PROFESSIONAL_REVIEW
    assert plan.scope_type is TrustPermissionScope.GLOBAL
    assert plan.facility_id is None
    assert plan.revocation_reason_code is RevocationReasonCode.ROLE_CHANGED
    assert plan.governance_reference == "TICKET-1234"
    assert (
        plan.revoke_event is ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED
    )


def test_revoke_self_subordinate_grant_is_allowed():
    actor_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    # Actor revoking their own subordinate permission is allowed
    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=actor_id,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        scope_type=TrustPermissionScope.FACILITY,
        facility_id=uuid4(),
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=actor_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.ACCESS_REMOVED,
    )

    plan = plan_revoke_permission(facts, now=now)
    assert plan.command is TrustPermissionCommand.REVOKE
    assert plan.target_provider_id == actor_id


def test_revoke_already_revoked_grant_denied():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=now - timedelta(days=1),
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.GOVERNANCE_CHANGE,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_ALREADY_REVOKED"


def test_revoke_root_permission_denied_offline_only():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.SECURITY_RESPONSE,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "ROOT_PERMISSION_OFFLINE_ONLY"


def test_revoke_disallows_client_selected_expired_superseded_reason():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.EXPIRED_SUPERSEDED,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "INVALID_REVOCATION_REASON"


# ---------------------------------------------------------------------------
# Mandatory Hardening Tests: Authoritative Grant State Validation (REVOKE)
# ---------------------------------------------------------------------------


def test_revoke_denies_wrong_grant_id_fact():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    different_grant_id = uuid4()
    now = datetime.now(timezone.utc)

    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=different_grant_id,  # Mismatched grant ID
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_revoke_denies_wrong_target_provider_id_fact():
    actor_id = uuid4()
    target_id = uuid4()
    other_provider_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=other_provider_id,  # Mismatched provider ID
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_revoke_denies_malformed_permission_scope_binding():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    # Grant with scope_type mismatched with permission
    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.FACILITY,  # Mismatch: PROFESSIONAL_REVIEW is GLOBAL
        facility_id=uuid4(),
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_revoke_denies_naive_revoked_or_validity_timestamp():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    # Naive timestamp in target grant
    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=datetime(2025, 1, 1, 0, 0, 0),  # Naive
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_revoke_denies_malformed_scope_facility_binding():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    # Facility permission with facility_id=None
    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        scope_type=TrustPermissionScope.FACILITY,
        facility_id=None,  # Malformed: FACILITY must have non-null facility_id
        valid_from=now - timedelta(days=10),
        valid_until=None,
        revoked_at=None,
    )

    facts = RevokeRequestFacts(
        actor_provider_id=actor_id,
        target_grant_id=grant_id,
        target_provider_id=target_id,
        target_grant=grant,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
    )

    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_revoke_permission(facts, now=now)
    assert exc_info.value.code == "GRANT_STATE_INVALID"


def test_governance_reference_length_and_format_validation():
    actor_id = uuid4()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    # Empty string is invalid
    f1 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        governance_reference="   ",
        target_eligibility=_active_target_eligibility(),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f1, now=now)
    assert exc_info.value.code == "INVALID_GOVERNANCE_REFERENCE"

    # Too long (> 128 chars)
    f2 = GrantRequestFacts(
        actor_provider_id=actor_id,
        target_provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        governance_reference="A" * 129,
        target_eligibility=_active_target_eligibility(),
    )
    with pytest.raises(TrustPermissionPolicyError) as exc_info:
        plan_grant_permission(f2, now=now)
    assert exc_info.value.code == "INVALID_GOVERNANCE_REFERENCE"


def test_plans_are_frozen_and_immutable():
    actor_id = uuid4()
    target_id = uuid4()
    grant_id = uuid4()
    now = datetime.now(timezone.utc)

    grant_plan = plan_grant_permission(
        GrantRequestFacts(
            actor_provider_id=actor_id,
            target_provider_id=target_id,
            permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
            target_eligibility=_active_target_eligibility(),
        ),
        now=now,
    )

    with pytest.raises(FrozenInstanceError):
        grant_plan.permission = TrustManagementPermission.FACILITY_REVIEW  # type: ignore

    grant = ExistingGrantSlotFacts(
        grant_id=grant_id,
        provider_id=target_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=now,
        valid_until=None,
        revoked_at=None,
    )
    revoke_plan = plan_revoke_permission(
        RevokeRequestFacts(
            actor_provider_id=actor_id,
            target_grant_id=grant_id,
            target_provider_id=target_id,
            target_grant=grant,
            revocation_reason_code=RevocationReasonCode.ACCESS_REMOVED,
        ),
        now=now,
    )

    with pytest.raises(FrozenInstanceError):
        revoke_plan.revocation_reason_code = RevocationReasonCode.SECURITY_RESPONSE  # type: ignore


def test_pure_policy_module_architecture_isolation():
    """Verify that policy module has zero database, ORM, Redis, or HTTP framework dependencies."""
    import ast

    source = inspect.getsource(policy_module)
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)

    forbidden_module_prefixes = [
        "sqlalchemy",
        "fastapi",
        "starlette",
        "redis",
        "app.models",
        "app.core.database",
        "app.services.audit_outbox",
    ]
    for imported in imported_modules:
        for forbidden in forbidden_module_prefixes:
            assert not imported.startswith(
                forbidden
            ), f"Forbidden module import '{imported}' found in pure policy module!"


def test_audit_event_literals_and_domain_invariants():
    """Verify audit event additions and confirm AuditDomain.SECURITY does NOT exist."""
    assert hasattr(ProviderTrustAuditEvent, "PROVIDER_TRUST_PERMISSION_GRANTED")
    assert hasattr(ProviderTrustAuditEvent, "PROVIDER_TRUST_PERMISSION_REVOKED")
    assert not hasattr(ProviderTrustAuditEvent, "PROVIDER_TRUST_PERMISSION_EXPIRED")

    # Confirm AuditDomain uses PLATFORM and no SECURITY domain was introduced
    assert hasattr(AuditDomain, "PLATFORM")
    assert not hasattr(AuditDomain, "SECURITY")
