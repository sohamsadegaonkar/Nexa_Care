"""Pure domain policy for subordinate organizational permission grant and revoke decisions.

This module intentionally contains no persistence, authorization, HTTP,
audit-outbox, Redis, or idempotency dependencies. It evaluates server-owned
facts and produces immutable mutation plans for Phase 4C to execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
    scope_for_permission,
)

_MAX_GOVERNANCE_REFERENCE_LENGTH = 128

_ORDINARY_PERMISSIONS = frozenset(
    {
        TrustManagementPermission.PROFESSIONAL_REVIEW,
        TrustManagementPermission.FACILITY_REVIEW,
        TrustManagementPermission.AFFILIATION_MANAGE,
    }
)


class TrustPermissionPolicyError(ValueError):
    """Stable, deterministic domain policy failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TrustPermissionCommand(str, Enum):
    """Closed ordinary permission administration command vocabulary."""

    GRANT = "GRANT"
    REVOKE = "REVOKE"


class RevocationReasonCode(str, Enum):
    """Closed server-owned revocation reason vocabulary."""

    ACCESS_REMOVED = "ACCESS_REMOVED"
    ROLE_CHANGED = "ROLE_CHANGED"
    SECURITY_RESPONSE = "SECURITY_RESPONSE"
    GOVERNANCE_CHANGE = "GOVERNANCE_CHANGE"
    EXPIRED_SUPERSEDED = "EXPIRED_SUPERSEDED"


@dataclass(frozen=True, slots=True)
class TargetProviderEligibilityFacts:
    """Authoritative target provider state supplied by the application layer."""

    provider_exists: bool
    account_is_active: bool
    account_status: str
    credential_exists: bool
    credential_is_active: bool


@dataclass(frozen=True, slots=True)
class ExistingGrantSlotFacts:
    """Authoritative grant state for an exact permission slot."""

    grant_id: UUID
    provider_id: UUID
    permission: TrustManagementPermission | str
    scope_type: TrustPermissionScope | str
    facility_id: UUID | None
    valid_from: datetime | None
    valid_until: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class GrantRequestFacts:
    """Normalized facts required to plan an ordinary subordinate permission grant."""

    actor_provider_id: UUID
    target_provider_id: UUID
    permission: TrustManagementPermission
    facility_id: UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    governance_reference: str | None = None
    target_eligibility: TargetProviderEligibilityFacts | None = None
    existing_slot_grant: ExistingGrantSlotFacts | None = None


@dataclass(frozen=True, slots=True)
class RevokeRequestFacts:
    """Normalized facts required to plan an ordinary subordinate permission revocation."""

    actor_provider_id: UUID
    target_grant_id: UUID
    target_provider_id: UUID
    target_grant: ExistingGrantSlotFacts
    revocation_reason_code: RevocationReasonCode
    governance_reference: str | None = None


@dataclass(frozen=True, slots=True)
class GrantPlan:
    """Immutable mutation plan for a subordinate permission grant."""

    command: TrustPermissionCommand
    target_provider_id: UUID
    permission: TrustManagementPermission
    scope_type: TrustPermissionScope
    facility_id: UUID | None
    valid_from: datetime | None
    valid_until: datetime | None
    governance_reference: str | None
    superseded_grant_id: UUID | None
    superseded_revocation_reason: RevocationReasonCode | None
    grant_event: ProviderTrustAuditEvent
    superseded_revoke_event: ProviderTrustAuditEvent | None


@dataclass(frozen=True, slots=True)
class RevokePlan:
    """Immutable mutation plan for an explicit permission revocation."""

    command: TrustPermissionCommand
    target_grant_id: UUID
    target_provider_id: UUID
    permission: TrustManagementPermission
    scope_type: TrustPermissionScope
    facility_id: UUID | None
    revocation_reason_code: RevocationReasonCode
    governance_reference: str | None
    revoke_event: ProviderTrustAuditEvent


def _normalize_aware_utc(dt: datetime, field_name: str) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise TrustPermissionPolicyError("INVALID_DATETIME_TIMEZONE")
    return dt.astimezone(timezone.utc)


def _validate_governance_reference(ref: str | None) -> str | None:
    if ref is None:
        return None
    if not isinstance(ref, str):
        raise TrustPermissionPolicyError("INVALID_GOVERNANCE_REFERENCE")
    cleaned = ref.strip()
    if not cleaned or len(cleaned) > _MAX_GOVERNANCE_REFERENCE_LENGTH:
        raise TrustPermissionPolicyError("INVALID_GOVERNANCE_REFERENCE")
    return cleaned


def plan_grant_permission(facts: GrantRequestFacts, now: datetime) -> GrantPlan:
    """Evaluate subordinate permission grant facts and return an immutable GrantPlan.

    Fails closed on any invalid state, self-grant, inactive target account/credential,
    scope mismatch, active slot collision, or root-permission request.
    """
    moment = _normalize_aware_utc(now, "now")

    # 1. Root permission boundary
    if facts.permission is TrustManagementPermission.TRUST_PERMISSION_MANAGE:
        raise TrustPermissionPolicyError("ROOT_PERMISSION_OFFLINE_ONLY")
    if facts.permission not in _ORDINARY_PERMISSIONS:
        raise TrustPermissionPolicyError("INVALID_PERMISSION")

    # 2. Self-grant prohibition
    if facts.actor_provider_id == facts.target_provider_id:
        raise TrustPermissionPolicyError("SELF_GRANT_PROHIBITED")

    # 3. Target provider eligibility
    eligibility = facts.target_eligibility
    if eligibility is None or not eligibility.provider_exists:
        raise TrustPermissionPolicyError("TARGET_PROVIDER_NOT_FOUND")
    if not eligibility.account_is_active or eligibility.account_status != "active":
        raise TrustPermissionPolicyError("TARGET_PROVIDER_INACTIVE")
    if not eligibility.credential_exists or not eligibility.credential_is_active:
        raise TrustPermissionPolicyError("TARGET_CREDENTIAL_INACTIVE")

    # 4. Scope derivation & binding validation
    expected_scope = scope_for_permission(facts.permission)
    if expected_scope is TrustPermissionScope.GLOBAL:
        if facts.facility_id is not None:
            raise TrustPermissionPolicyError("GLOBAL_PERMISSION_FACILITY_PROHIBITED")
    elif expected_scope is TrustPermissionScope.FACILITY:
        if facts.facility_id is None:
            raise TrustPermissionPolicyError("FACILITY_PERMISSION_FACILITY_REQUIRED")

    # 5. Temporal validity validation
    from_utc = (
        _normalize_aware_utc(facts.valid_from, "valid_from")
        if facts.valid_from is not None
        else None
    )
    until_utc = (
        _normalize_aware_utc(facts.valid_until, "valid_until")
        if facts.valid_until is not None
        else None
    )
    if from_utc is not None and until_utc is not None and until_utc <= from_utc:
        raise TrustPermissionPolicyError("INVALID_VALIDITY_INTERVAL")

    # 6. Governance reference validation
    gov_ref = _validate_governance_reference(facts.governance_reference)

    # 7. Slot conflict and expired-unrevoked supersession analysis
    superseded_grant_id: UUID | None = None
    superseded_revocation_reason: RevocationReasonCode | None = None
    superseded_revoke_event: ProviderTrustAuditEvent | None = None

    existing = facts.existing_slot_grant
    if existing is not None:
        # Mandatory authoritative grant state validation
        if not isinstance(existing, ExistingGrantSlotFacts):
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        if existing.provider_id != facts.target_provider_id:
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        try:
            existing_perm = (
                existing.permission
                if isinstance(existing.permission, TrustManagementPermission)
                else TrustManagementPermission(str(existing.permission))
            )
            existing_scope = (
                existing.scope_type
                if isinstance(existing.scope_type, TrustPermissionScope)
                else TrustPermissionScope(str(existing.scope_type))
            )
        except (ValueError, TypeError):
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        if existing_perm is not facts.permission:
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        if existing_scope is not expected_scope:
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        if expected_scope is TrustPermissionScope.GLOBAL:
            if existing.facility_id is not None:
                raise TrustPermissionPolicyError("GRANT_STATE_INVALID")
        elif expected_scope is TrustPermissionScope.FACILITY:
            if existing.facility_id != facts.facility_id:
                raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        # Validate timezone awareness of timestamps on the existing row
        for ts_val in (existing.valid_from, existing.valid_until, existing.revoked_at):
            if ts_val is not None:
                if (
                    not isinstance(ts_val, datetime)
                    or ts_val.tzinfo is None
                    or ts_val.utcoffset() is None
                ):
                    raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        if existing.valid_from is not None and existing.valid_until is not None:
            if existing.valid_until <= existing.valid_from:
                raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

        if existing.revoked_at is None:
            # Grant slot is still claimed in PostgreSQL partial unique index
            existing_until = (
                _normalize_aware_utc(existing.valid_until, "existing_valid_until")
                if existing.valid_until is not None
                else None
            )
            if existing_until is None or existing_until > moment:
                # Active or future-effective grant exists
                raise TrustPermissionPolicyError("ACTIVE_GRANT_EXISTS")
            # Expired but unrevoked: produces governed internal supersession
            superseded_grant_id = existing.grant_id
            superseded_revocation_reason = RevocationReasonCode.EXPIRED_SUPERSEDED
            superseded_revoke_event = (
                ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED
            )

    return GrantPlan(
        command=TrustPermissionCommand.GRANT,
        target_provider_id=facts.target_provider_id,
        permission=facts.permission,
        scope_type=expected_scope,
        facility_id=facts.facility_id,
        valid_from=from_utc,
        valid_until=until_utc,
        governance_reference=gov_ref,
        superseded_grant_id=superseded_grant_id,
        superseded_revocation_reason=superseded_revocation_reason,
        grant_event=ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_GRANTED,
        superseded_revoke_event=superseded_revoke_event,
    )


def plan_revoke_permission(facts: RevokeRequestFacts, now: datetime) -> RevokePlan:
    """Evaluate subordinate permission revocation facts and return an immutable RevokePlan.

    Fails closed if the grant does not exist, has invalid state, is already revoked, targets root
    authority, or specifies a client-disallowed revocation reason code. Self-revocation of
    subordinate permissions is allowed.
    """
    _normalize_aware_utc(now, "now")

    target = facts.target_grant
    if target is None or not isinstance(target, ExistingGrantSlotFacts):
        raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    # Authoritative ID binding validation
    if (
        target.grant_id != facts.target_grant_id
        or target.provider_id != facts.target_provider_id
    ):
        raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    # Permission parses to the closed permission enum
    try:
        parsed_perm = (
            target.permission
            if isinstance(target.permission, TrustManagementPermission)
            else TrustManagementPermission(str(target.permission))
        )
    except (ValueError, TypeError):
        raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    # Scope validation against parsed permission
    try:
        expected_target_scope = scope_for_permission(parsed_perm)
    except (TypeError, KeyError):
        raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    try:
        target_scope = (
            target.scope_type
            if isinstance(target.scope_type, TrustPermissionScope)
            else TrustPermissionScope(str(target.scope_type))
        )
    except (ValueError, TypeError):
        raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    if target_scope is not expected_target_scope:
        raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    if expected_target_scope is TrustPermissionScope.GLOBAL:
        if target.facility_id is not None:
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")
    elif expected_target_scope is TrustPermissionScope.FACILITY:
        if target.facility_id is None:
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    # All present lifecycle timestamps are timezone-aware
    for ts_val in (target.valid_from, target.valid_until, target.revoked_at):
        if ts_val is not None:
            if (
                not isinstance(ts_val, datetime)
                or ts_val.tzinfo is None
                or ts_val.utcoffset() is None
            ):
                raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    if target.valid_from is not None and target.valid_until is not None:
        if target.valid_until <= target.valid_from:
            raise TrustPermissionPolicyError("GRANT_STATE_INVALID")

    # Root permission boundary
    if parsed_perm is TrustManagementPermission.TRUST_PERMISSION_MANAGE:
        raise TrustPermissionPolicyError("ROOT_PERMISSION_OFFLINE_ONLY")
    if parsed_perm not in _ORDINARY_PERMISSIONS:
        raise TrustPermissionPolicyError("INVALID_PERMISSION")

    # Already revoked check
    if target.revoked_at is not None:
        raise TrustPermissionPolicyError("GRANT_ALREADY_REVOKED")

    # Revocation reason validation
    reason = facts.revocation_reason_code
    if not isinstance(reason, RevocationReasonCode):
        raise TrustPermissionPolicyError("INVALID_REVOCATION_REASON")
    if reason is RevocationReasonCode.EXPIRED_SUPERSEDED:
        # EXPIRED_SUPERSEDED is server-owned for grant supersession only, never client-selected
        raise TrustPermissionPolicyError("INVALID_REVOCATION_REASON")

    # Governance reference validation
    gov_ref = _validate_governance_reference(facts.governance_reference)

    return RevokePlan(
        command=TrustPermissionCommand.REVOKE,
        target_grant_id=target.grant_id,
        target_provider_id=target.provider_id,
        permission=parsed_perm,
        scope_type=expected_target_scope,
        facility_id=target.facility_id,
        revocation_reason_code=reason,
        governance_reference=gov_ref,
        revoke_event=ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED,
    )
