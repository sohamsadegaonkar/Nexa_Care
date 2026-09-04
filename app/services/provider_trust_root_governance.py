"""Authoritative offline service for organizational root-of-trust administration.

TRUST_PERMISSION_MANAGE is Tier-0 root authority and cannot be minted or revoked
through public HTTP or ordinary provider sessions. This service executes strictly
offline via a dedicated database connection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event
from app.services.policy_service import validate_idempotency_key

# Global PostgreSQL transaction advisory lock key for all root governance mutations.
# Derived deterministically from namespace "nexa:provider_trust:root_governance:v1".
_GLOBAL_ROOT_GOVERNANCE_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"nexa:provider_trust:root_governance:v1").digest()[:8],
    byteorder="big",
    signed=True,
)

_IDEMPOTENCY_TENANT = "platform-provider-trust"
_OPERATION_GRANT_ROOT = "provider.trust.root.grant.v1"
_OPERATION_REVOKE_ROOT = "provider.trust.root.revoke.v1"

_ROOT_PERMISSION_STR = "TRUST_PERMISSION_MANAGE"
_ROOT_SCOPE_STR = "GLOBAL"
_MAX_ROOT_LIFETIME = timedelta(days=90)

_IDEMPOTENCY_SELECT = text("""
    SELECT request_hash, response_status, response_payload
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :key
""")
_IDEMPOTENCY_RESERVE = text("""
    INSERT INTO public.mutation_idempotency
      (tenant_id, actor_id, operation, resource_id, idempotency_key, request_hash, created_at, retention_expires_at)
    VALUES (:tenant_id, :actor_id, :operation, :resource_id, :key, :request_hash, now(), now() + interval '90 days')
    ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
    RETURNING id
""")
_IDEMPOTENCY_COMPLETE = text("""
    UPDATE public.mutation_idempotency
    SET response_status = 200, response_payload = CAST(:payload AS JSONB), resulting_resource_version = NULL
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :key
""")


class TrustRootGovernanceCommand(str, Enum):
    GRANT_ROOT = "GRANT_ROOT"
    REVOKE_ROOT = "REVOKE_ROOT"


class RootRevocationReasonCode(str, Enum):
    ACCESS_REMOVED = "ACCESS_REMOVED"
    SECURITY_RESPONSE = "SECURITY_RESPONSE"
    GOVERNANCE_CHANGE = "GOVERNANCE_CHANGE"
    ROOT_ROTATION = "ROOT_ROTATION"
    COMPROMISE_RESPONSE = "COMPROMISE_RESPONSE"
    EXPIRED_SUPERSEDED = "EXPIRED_SUPERSEDED"


OPERATOR_REVOCATION_REASONS = frozenset(
    {
        RootRevocationReasonCode.ACCESS_REMOVED,
        RootRevocationReasonCode.SECURITY_RESPONSE,
        RootRevocationReasonCode.GOVERNANCE_CHANGE,
        RootRevocationReasonCode.ROOT_ROTATION,
        RootRevocationReasonCode.COMPROMISE_RESPONSE,
    }
)


class ProviderTrustRootGovernanceError(RuntimeError):
    """Stable failure for root-of-trust administration."""

    def __init__(self, code: str, *, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True, slots=True)
class ProviderTrustRootGovernanceResult:
    """Safe structural result of an offline root grant or revoke operation."""

    command: str
    grant_id: UUID
    target_provider_id: UUID
    permission: str
    scope_type: str
    valid_from: datetime | None
    valid_until: datetime | None
    superseded_grant_id: UUID | None
    event_types: list[str]
    idempotent_replay: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "grant_id": str(self.grant_id),
            "target_provider_id": str(self.target_provider_id),
            "permission": self.permission,
            "scope_type": self.scope_type,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "superseded_grant_id": (
                str(self.superseded_grant_id) if self.superseded_grant_id else None
            ),
            "event_types": list(self.event_types),
            "idempotent_replay": self.idempotent_replay,
        }


def _validate_and_normalize_evidence(
    operator_actor_id: str,
    approver_actor_id: str,
    governance_reference: str,
) -> tuple[str, str, str]:
    if (
        not isinstance(operator_actor_id, str)
        or not isinstance(approver_actor_id, str)
        or not isinstance(governance_reference, str)
    ):
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST")
    op = operator_actor_id.strip()
    appr = approver_actor_id.strip()
    gov_ref = governance_reference.strip()
    if not op or len(op) > 128:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST")
    if not appr or len(appr) > 128:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST")
    if not gov_ref or len(gov_ref) > 128:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST")
    if op == appr:
        raise ProviderTrustRootGovernanceError("INVALID_REQUEST")
    return op, appr, gov_ref


def _request_hash_grant_root(
    *,
    operator_actor_id: str,
    approver_actor_id: str,
    target_provider_id: UUID,
    governance_reference: str,
    expected_active_root_count: int,
    valid_until: datetime,
) -> str:
    canonical = {
        "approver_actor_id": approver_actor_id,
        "expected_active_root_count": expected_active_root_count,
        "governance_reference": governance_reference,
        "operation": _OPERATION_GRANT_ROOT,
        "operator_actor_id": operator_actor_id,
        "target_provider_id": str(target_provider_id),
        "valid_until": valid_until.astimezone(timezone.utc).isoformat(),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _request_hash_revoke_root(
    *,
    operator_actor_id: str,
    approver_actor_id: str,
    grant_id: UUID,
    governance_reference: str,
    revocation_reason_code: str,
    expected_active_root_count: int,
    acknowledge_zero_active_roots: bool,
) -> str:
    canonical = {
        "acknowledge_zero_active_roots": bool(acknowledge_zero_active_roots),
        "approver_actor_id": approver_actor_id,
        "expected_active_root_count": expected_active_root_count,
        "governance_reference": governance_reference,
        "grant_id": str(grant_id),
        "operation": _OPERATION_REVOKE_ROOT,
        "operator_actor_id": operator_actor_id,
        "revocation_reason_code": str(revocation_reason_code),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ProviderTrustRootGovernanceService:
    """Authoritative service for offline root-of-trust mutations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _replay(
        self,
        existing_row: Any,
        expected_hash: str,
    ) -> ProviderTrustRootGovernanceResult:
        if existing_row.request_hash != expected_hash:
            raise ProviderTrustRootGovernanceError("IDEMPOTENCY_KEY_REUSED")
        if existing_row.response_status != 200 or not existing_row.response_payload:
            raise ProviderTrustRootGovernanceError("IDEMPOTENCY_IN_PROGRESS")
        try:
            payload = (
                existing_row.response_payload
                if isinstance(existing_row.response_payload, dict)
                else json.loads(existing_row.response_payload)
            )
            return ProviderTrustRootGovernanceResult(
                command=payload["command"],
                grant_id=UUID(payload["grant_id"]),
                target_provider_id=UUID(payload["target_provider_id"]),
                permission=payload["permission"],
                scope_type=payload["scope_type"],
                valid_from=(
                    datetime.fromisoformat(payload["valid_from"])
                    if payload.get("valid_from")
                    else None
                ),
                valid_until=(
                    datetime.fromisoformat(payload["valid_until"])
                    if payload.get("valid_until")
                    else None
                ),
                superseded_grant_id=(
                    UUID(payload["superseded_grant_id"])
                    if payload.get("superseded_grant_id")
                    else None
                ),
                event_types=list(payload["event_types"]),
                idempotent_replay=True,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderTrustRootGovernanceError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc

    async def _acquire_global_lock(self) -> None:
        """Acquire the server-owned transaction advisory lock for root operations."""
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _GLOBAL_ROOT_GOVERNANCE_LOCK_KEY},
        )

    async def _count_effective_active_roots(self, moment: datetime) -> int:
        """Count currently effective, unrevoked root grants."""
        stmt = select(func.count(ProviderTrustPermissionGrant.id)).where(
            ProviderTrustPermissionGrant.permission == _ROOT_PERMISSION_STR,
            ProviderTrustPermissionGrant.revoked_at.is_(None),
            (
                ProviderTrustPermissionGrant.valid_from.is_(None)
                | (ProviderTrustPermissionGrant.valid_from <= moment)
            ),
            (
                ProviderTrustPermissionGrant.valid_until.is_(None)
                | (ProviderTrustPermissionGrant.valid_until > moment)
            ),
        )
        return (await self.db.execute(stmt)).scalar() or 0

    async def _lock_target_provider_rows(
        self,
        target_provider_id: UUID,
    ) -> tuple[
        ProviderIdentity | None,
        ProviderCredential | None,
        list[ProviderTrustPermissionGrant],
    ]:
        """Lock ProviderIdentity, ProviderCredential, and ProviderTrustPermissionGrants in deterministic order."""
        ident_res = await self.db.execute(
            select(ProviderIdentity)
            .where(ProviderIdentity.id == target_provider_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        ident = ident_res.scalar_one_or_none()

        cred_res = await self.db.execute(
            select(ProviderCredential)
            .where(ProviderCredential.provider_id == target_provider_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        cred = cred_res.scalar_one_or_none()
        if ident is not None:
            ident.credential = cred

        grants_res = await self.db.execute(
            select(ProviderTrustPermissionGrant)
            .where(ProviderTrustPermissionGrant.provider_id == target_provider_id)
            .order_by(ProviderTrustPermissionGrant.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        grants = list(grants_res.scalars().all())

        return ident, cred, grants

    async def grant_root(
        self,
        *,
        operator_actor_id: str,
        approver_actor_id: str,
        target_provider_id: UUID,
        valid_until: datetime,
        expected_active_root_count: int,
        governance_reference: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderTrustRootGovernanceResult:
        """Execute an authoritative offline root grant."""
        op, appr, gov_ref = _validate_and_normalize_evidence(
            operator_actor_id, approver_actor_id, governance_reference
        )

        if not isinstance(target_provider_id, UUID):
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        if (
            not isinstance(expected_active_root_count, int)
            or expected_active_root_count < 0
        ):
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        try:
            key = validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST") from exc

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        if (
            valid_until is None
            or valid_until.tzinfo is None
            or valid_until.utcoffset() is None
        ):
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")
        if valid_until <= moment or valid_until > moment + _MAX_ROOT_LIFETIME:
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        request_hash = _request_hash_grant_root(
            operator_actor_id=op,
            approver_actor_id=appr,
            target_provider_id=target_provider_id,
            governance_reference=gov_ref,
            expected_active_root_count=expected_active_root_count,
            valid_until=valid_until,
        )

        try:
            async with self.db.begin():
                # 1. Idempotency reservation / replay check
                existing = (
                    await self.db.execute(
                        _IDEMPOTENCY_SELECT,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "operation": _OPERATION_GRANT_ROOT,
                            "key": key,
                        },
                    )
                ).first()
                if existing is not None:
                    return self._replay(existing, request_hash)

                reserved = (
                    await self.db.execute(
                        _IDEMPOTENCY_RESERVE,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "actor_id": op,
                            "operation": _OPERATION_GRANT_ROOT,
                            "resource_id": str(target_provider_id),
                            "key": key,
                            "request_hash": request_hash,
                        },
                    )
                ).first()
                if reserved is None:
                    existing = (
                        await self.db.execute(
                            _IDEMPOTENCY_SELECT,
                            {
                                "tenant_id": _IDEMPOTENCY_TENANT,
                                "operation": _OPERATION_GRANT_ROOT,
                                "key": key,
                            },
                        )
                    ).first()
                    if existing is None:
                        raise ProviderTrustRootGovernanceError(
                            "IDEMPOTENCY_IN_PROGRESS"
                        )
                    return self._replay(existing, request_hash)

                # 2. Acquire global root governance advisory lock
                await self._acquire_global_lock()

                # 3. Verify expected active root count CAS
                actual_active_roots = await self._count_effective_active_roots(moment)
                if actual_active_roots != expected_active_root_count:
                    raise ProviderTrustRootGovernanceError("ROOT_SET_CHANGED")

                # 4. Lock target provider rows in deterministic order
                ident, cred, grants = await self._lock_target_provider_rows(
                    target_provider_id
                )

                # 5. Verify target eligibility
                if ident is None:
                    raise ProviderTrustRootGovernanceError("TARGET_PROVIDER_NOT_FOUND")
                if not ident.is_active or ident.status != "active":
                    raise ProviderTrustRootGovernanceError("TARGET_PROVIDER_INACTIVE")
                if cred is None or not cred.is_active:
                    raise ProviderTrustRootGovernanceError("TARGET_CREDENTIAL_INACTIVE")
                if ident.email_verified_at is None or ident.phone_verified_at is None:
                    raise ProviderTrustRootGovernanceError(
                        "TARGET_CONTACT_ASSURANCE_INCOMPLETE"
                    )
                if not cred.mfa_enabled or cred.mfa_secret_encrypted is None:
                    raise ProviderTrustRootGovernanceError("TARGET_MFA_NOT_CONFIGURED")

                # 6. Check existing root grants for target
                unrevoked_roots = [
                    g
                    for g in grants
                    if g.permission == _ROOT_PERMISSION_STR and g.revoked_at is None
                ]
                active_or_future = [
                    g
                    for g in unrevoked_roots
                    if g.valid_until is None or g.valid_until > moment
                ]
                if active_or_future:
                    raise ProviderTrustRootGovernanceError("ACTIVE_ROOT_GRANT_EXISTS")

                expired_roots = [
                    g
                    for g in unrevoked_roots
                    if g.valid_until is not None and g.valid_until <= moment
                ]
                if len(expired_roots) > 1:
                    raise ProviderTrustRootGovernanceError(
                        "TRANSACTION_INTEGRITY_FAILURE"
                    )

                superseded_grant_id: UUID | None = None
                event_types: list[str] = []

                # Expired-root supersession
                if expired_roots:
                    expired_grant = expired_roots[0]
                    expired_grant.revoked_at = moment
                    superseded_grant_id = expired_grant.id
                    event_types.append(
                        ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED.value
                    )

                # 7. Create new root grant
                new_grant_id = uuid4()
                new_grant = ProviderTrustPermissionGrant(
                    id=new_grant_id,
                    provider_id=target_provider_id,
                    permission=_ROOT_PERMISSION_STR,
                    scope_type=_ROOT_SCOPE_STR,
                    facility_id=None,
                    granted_at=moment,
                    valid_from=moment,
                    valid_until=valid_until,
                    revoked_at=None,
                    granted_by_actor_id=op,
                    governance_reference=gov_ref,
                )
                self.db.add(new_grant)
                event_types.append(
                    ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_GRANTED.value
                )

                audit_context = AuditContext.platform(domain=AuditDomain.PLATFORM)

                # Stage supersession revoke audit if applicable
                if superseded_grant_id is not None:
                    supersede_audit_key = f"provider-trust-root:supersede-revoke:{superseded_grant_id}:{key}"
                    await enqueue_audit_event(
                        self.db,
                        audit_context=audit_context,
                        idempotency_key=supersede_audit_key,
                        actor_id=op,
                        event_type=ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED.value,
                        target_id=str(superseded_grant_id),
                        patient_id=None,
                        metadata={
                            "command": "REVOKE_ROOT",
                            "governance_mode": "OFFLINE_ROOT",
                            "operator_actor_id": op,
                            "approver_actor_id": appr,
                            "governance_reference": gov_ref,
                            "target_provider_id": str(target_provider_id),
                            "grant_id": str(superseded_grant_id),
                            "permission": _ROOT_PERMISSION_STR,
                            "scope_type": _ROOT_SCOPE_STR,
                            "valid_from": (
                                expired_grant.valid_from.isoformat()
                                if expired_grant.valid_from
                                else None
                            ),
                            "valid_until": (
                                expired_grant.valid_until.isoformat()
                                if expired_grant.valid_until
                                else None
                            ),
                            "revocation_reason_code": RootRevocationReasonCode.EXPIRED_SUPERSEDED.value,
                            "expected_active_root_count": expected_active_root_count,
                            "superseded_by_grant_id": str(new_grant_id),
                        },
                    )

                # Stage grant audit event
                grant_audit_key = f"provider-trust-root:grant:{new_grant_id}:{key}"
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=grant_audit_key,
                    actor_id=op,
                    event_type=ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_GRANTED.value,
                    target_id=str(new_grant_id),
                    patient_id=None,
                    metadata={
                        "command": "GRANT_ROOT",
                        "governance_mode": "OFFLINE_ROOT",
                        "operator_actor_id": op,
                        "approver_actor_id": appr,
                        "governance_reference": gov_ref,
                        "target_provider_id": str(target_provider_id),
                        "grant_id": str(new_grant_id),
                        "permission": _ROOT_PERMISSION_STR,
                        "scope_type": _ROOT_SCOPE_STR,
                        "valid_from": moment.isoformat(),
                        "valid_until": valid_until.isoformat(),
                        "expected_active_root_count": expected_active_root_count,
                        "superseded_grant_id": (
                            str(superseded_grant_id) if superseded_grant_id else None
                        ),
                    },
                )

                # 8. Complete idempotency record
                result = ProviderTrustRootGovernanceResult(
                    command=TrustRootGovernanceCommand.GRANT_ROOT.value,
                    grant_id=new_grant_id,
                    target_provider_id=target_provider_id,
                    permission=_ROOT_PERMISSION_STR,
                    scope_type=_ROOT_SCOPE_STR,
                    valid_from=moment,
                    valid_until=valid_until,
                    superseded_grant_id=superseded_grant_id,
                    event_types=event_types,
                    idempotent_replay=False,
                )
                payload_json = json.dumps(
                    result.to_dict(), sort_keys=True, separators=(",", ":")
                )
                await self.db.execute(
                    _IDEMPOTENCY_COMPLETE,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": _OPERATION_GRANT_ROOT,
                        "key": key,
                        "payload": payload_json,
                    },
                )
                return result
        except ProviderTrustRootGovernanceError:
            raise
        except Exception as exc:
            raise ProviderTrustRootGovernanceError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc

    async def revoke_root(
        self,
        *,
        operator_actor_id: str,
        approver_actor_id: str,
        grant_id: UUID,
        revocation_reason_code: RootRevocationReasonCode | str,
        expected_active_root_count: int,
        governance_reference: str,
        idempotency_key: str,
        acknowledge_zero_active_roots: bool = False,
        now: datetime | None = None,
    ) -> ProviderTrustRootGovernanceResult:
        """Execute an authoritative offline root revocation."""
        op, appr, gov_ref = _validate_and_normalize_evidence(
            operator_actor_id, approver_actor_id, governance_reference
        )

        if not isinstance(grant_id, UUID):
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        if (
            not isinstance(expected_active_root_count, int)
            or expected_active_root_count < 0
        ):
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        try:
            key = validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST") from exc

        # Reason code validation (closed operator vocabulary)
        if isinstance(revocation_reason_code, str):
            try:
                reason = RootRevocationReasonCode(revocation_reason_code)
            except ValueError as exc:
                raise ProviderTrustRootGovernanceError("INVALID_REQUEST") from exc
        elif isinstance(revocation_reason_code, RootRevocationReasonCode):
            reason = revocation_reason_code
        else:
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        if reason not in OPERATOR_REVOCATION_REASONS:
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ProviderTrustRootGovernanceError("INVALID_REQUEST")

        request_hash = _request_hash_revoke_root(
            operator_actor_id=op,
            approver_actor_id=appr,
            grant_id=grant_id,
            governance_reference=gov_ref,
            revocation_reason_code=reason.value,
            expected_active_root_count=expected_active_root_count,
            acknowledge_zero_active_roots=acknowledge_zero_active_roots,
        )

        try:
            async with self.db.begin():
                # 1. Idempotency reservation / replay check
                existing = (
                    await self.db.execute(
                        _IDEMPOTENCY_SELECT,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "operation": _OPERATION_REVOKE_ROOT,
                            "key": key,
                        },
                    )
                ).first()
                if existing is not None:
                    return self._replay(existing, request_hash)

                reserved = (
                    await self.db.execute(
                        _IDEMPOTENCY_RESERVE,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "actor_id": op,
                            "operation": _OPERATION_REVOKE_ROOT,
                            "resource_id": str(grant_id),
                            "key": key,
                            "request_hash": request_hash,
                        },
                    )
                ).first()
                if reserved is None:
                    existing = (
                        await self.db.execute(
                            _IDEMPOTENCY_SELECT,
                            {
                                "tenant_id": _IDEMPOTENCY_TENANT,
                                "operation": _OPERATION_REVOKE_ROOT,
                                "key": key,
                            },
                        )
                    ).first()
                    if existing is None:
                        raise ProviderTrustRootGovernanceError(
                            "IDEMPOTENCY_IN_PROGRESS"
                        )
                    return self._replay(existing, request_hash)

                # 2. Acquire global root governance advisory lock
                await self._acquire_global_lock()

                # 3. Non-locking discovery of grant to find target_provider_id
                target_provider_id = (
                    await self.db.execute(
                        select(ProviderTrustPermissionGrant.provider_id).where(
                            ProviderTrustPermissionGrant.id == grant_id
                        )
                    )
                ).scalar_one_or_none()
                if target_provider_id is None:
                    raise ProviderTrustRootGovernanceError("ROOT_GRANT_NOT_FOUND")

                # 4. Verify expected active root count CAS
                actual_active_roots = await self._count_effective_active_roots(moment)
                if actual_active_roots != expected_active_root_count:
                    raise ProviderTrustRootGovernanceError("ROOT_SET_CHANGED")

                # 5. Lock target provider rows in deterministic order
                # (Notice: no eligibility checks are performed on target provider; revocation must work against inactive/disabled accounts)
                _, _, target_grants = await self._lock_target_provider_rows(
                    target_provider_id
                )

                # 6. Locate target grant row
                locked_grant = next(
                    (g for g in target_grants if g.id == grant_id), None
                )
                if locked_grant is None:
                    raise ProviderTrustRootGovernanceError("ROOT_GRANT_NOT_FOUND")

                if (
                    locked_grant.permission != _ROOT_PERMISSION_STR
                    or locked_grant.scope_type != _ROOT_SCOPE_STR
                    or locked_grant.facility_id is not None
                ):
                    raise ProviderTrustRootGovernanceError("ROOT_STATE_INVALID")

                if locked_grant.revoked_at is not None:
                    raise ProviderTrustRootGovernanceError("ROOT_GRANT_ALREADY_REVOKED")

                # 7. Check if revoking this effective grant results in zero active roots
                is_effective = (
                    locked_grant.valid_from is None or locked_grant.valid_from <= moment
                ) and (
                    locked_grant.valid_until is None
                    or locked_grant.valid_until > moment
                )
                if is_effective:
                    resulting_active_roots = actual_active_roots - 1
                    if (
                        resulting_active_roots == 0
                        and not acknowledge_zero_active_roots
                    ):
                        raise ProviderTrustRootGovernanceError("ZERO_ROOT_ACK_REQUIRED")

                # 8. Mutate revoked_at only
                locked_grant.revoked_at = moment

                # 9. Stage revoke audit event
                revoke_audit_key = f"provider-trust-root:revoke:{grant_id}:{key}"
                audit_context = AuditContext.platform(domain=AuditDomain.PLATFORM)
                await enqueue_audit_event(
                    self.db,
                    audit_context=audit_context,
                    idempotency_key=revoke_audit_key,
                    actor_id=op,
                    event_type=ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED.value,
                    target_id=str(grant_id),
                    patient_id=None,
                    metadata={
                        "command": "REVOKE_ROOT",
                        "governance_mode": "OFFLINE_ROOT",
                        "operator_actor_id": op,
                        "approver_actor_id": appr,
                        "governance_reference": gov_ref,
                        "target_provider_id": str(target_provider_id),
                        "grant_id": str(grant_id),
                        "permission": _ROOT_PERMISSION_STR,
                        "scope_type": _ROOT_SCOPE_STR,
                        "valid_from": (
                            locked_grant.valid_from.isoformat()
                            if locked_grant.valid_from
                            else None
                        ),
                        "valid_until": (
                            locked_grant.valid_until.isoformat()
                            if locked_grant.valid_until
                            else None
                        ),
                        "revocation_reason_code": reason.value,
                        "expected_active_root_count": expected_active_root_count,
                    },
                )

                # 10. Complete idempotency record
                result = ProviderTrustRootGovernanceResult(
                    command=TrustRootGovernanceCommand.REVOKE_ROOT.value,
                    grant_id=grant_id,
                    target_provider_id=target_provider_id,
                    permission=_ROOT_PERMISSION_STR,
                    scope_type=_ROOT_SCOPE_STR,
                    valid_from=locked_grant.valid_from,
                    valid_until=locked_grant.valid_until,
                    superseded_grant_id=None,
                    event_types=[
                        ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED.value
                    ],
                    idempotent_replay=False,
                )
                payload_json = json.dumps(
                    result.to_dict(), sort_keys=True, separators=(",", ":")
                )
                await self.db.execute(
                    _IDEMPOTENCY_COMPLETE,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": _OPERATION_REVOKE_ROOT,
                        "key": key,
                        "payload": payload_json,
                    },
                )
                return result
        except ProviderTrustRootGovernanceError:
            raise
        except Exception as exc:
            raise ProviderTrustRootGovernanceError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc
