"""Current-state organizational authorization for future trust operations.

This service intentionally authorizes requests only.  It never applies a
lifecycle transition, writes an audit event, or grants clinical capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.provider import (
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
    scope_for_permission,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.provider_trust_lifecycle import (
    AffiliationTransitionCommand,
    FacilityTransitionCommand,
    ProfessionalTransitionCommand,
)


class TrustAuthorizationDenialCode(str, Enum):
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    CREDENTIAL_INACTIVE = "CREDENTIAL_INACTIVE"
    CONTACT_VERIFICATION_REQUIRED = "CONTACT_VERIFICATION_REQUIRED"
    PROVIDER_SESSION_REQUIRED = "PROVIDER_SESSION_REQUIRED"
    MFA_REQUIRED = "MFA_REQUIRED"
    MFA_SESSION_ASSURANCE_REQUIRED = "MFA_SESSION_ASSURANCE_REQUIRED"
    TRUST_PERMISSION_REQUIRED = "TRUST_PERMISSION_REQUIRED"
    TRUST_PERMISSION_REVOKED_OR_INACTIVE = "TRUST_PERMISSION_REVOKED_OR_INACTIVE"
    TRUST_PERMISSION_SCOPE_MISMATCH = "TRUST_PERMISSION_SCOPE_MISMATCH"
    TARGET_SCOPE_INVALID = "TARGET_SCOPE_INVALID"
    SELF_REVIEW_PROHIBITED = "SELF_REVIEW_PROHIBITED"
    SELF_AFFILIATION_MANAGEMENT_PROHIBITED = "SELF_AFFILIATION_MANAGEMENT_PROHIBITED"
    TRUST_PERMISSION_STATE_INVALID = "TRUST_PERMISSION_STATE_INVALID"


@dataclass(frozen=True, slots=True)
class TrustManagementAuthentication:
    provider_id: UUID
    method: ClinicalAuthenticationMethod
    session_authenticated: bool
    mfa_verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class TrustAuthorizationDecision:
    allowed: bool
    permission: TrustManagementPermission | None
    scope: TrustPermissionScope | None
    denial_code: TrustAuthorizationDenialCode | None


def professional_command_permission(
    command: ProfessionalTransitionCommand,
) -> TrustManagementPermission | None:
    if not isinstance(command, ProfessionalTransitionCommand):
        raise TypeError("command must be server-owned")
    if command is ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE:
        raise ValueError(
            "CANCEL_RECHECK_GRACE cannot be authorized for human reviewers"
        )
    return (
        None
        if command is ProfessionalTransitionCommand.SUBMIT
        else TrustManagementPermission.PROFESSIONAL_REVIEW
    )


def facility_command_permission(
    command: FacilityTransitionCommand,
) -> TrustManagementPermission:
    if not isinstance(command, FacilityTransitionCommand):
        raise TypeError("command must be server-owned")
    return TrustManagementPermission.FACILITY_REVIEW


def affiliation_command_permission(
    command: AffiliationTransitionCommand,
) -> TrustManagementPermission:
    if not isinstance(command, AffiliationTransitionCommand):
        raise TypeError("command must be server-owned")
    return TrustManagementPermission.AFFILIATION_MANAGE


class ProviderTrustAuthorizationService:
    """Evaluate PostgreSQL-authoritative organizational grants per request."""

    @staticmethod
    def _deny(
        code: TrustAuthorizationDenialCode,
        permission: TrustManagementPermission | None = None,
        scope: TrustPermissionScope | None = None,
    ) -> TrustAuthorizationDecision:
        return TrustAuthorizationDecision(False, permission, scope, code)

    @staticmethod
    def _allow(
        permission: TrustManagementPermission, scope: TrustPermissionScope
    ) -> TrustAuthorizationDecision:
        return TrustAuthorizationDecision(True, permission, scope, None)

    async def _actor(self, db: AsyncSession, actor_id: UUID) -> ProviderIdentity | None:
        result = await db.execute(
            select(ProviderIdentity)
            .where(ProviderIdentity.id == actor_id)
            .options(selectinload(ProviderIdentity.credential))
        )
        return result.scalar_one_or_none()

    async def _grants(
        self, db: AsyncSession, actor_id: UUID
    ) -> list[ProviderTrustPermissionGrant]:
        result = await db.execute(
            select(ProviderTrustPermissionGrant).where(
                ProviderTrustPermissionGrant.provider_id == actor_id
            )
        )
        return list(result.scalars().all())

    def _strong_auth(
        self,
        actor: ProviderIdentity | None,
        authentication: TrustManagementAuthentication,
        now: datetime,
    ) -> TrustAuthorizationDenialCode | None:
        if actor is None or not actor.is_active or actor.status != "active":
            return TrustAuthorizationDenialCode.ACCOUNT_INACTIVE
        if actor.credential is None or not actor.credential.is_active:
            return TrustAuthorizationDenialCode.CREDENTIAL_INACTIVE
        if (
            authentication.provider_id != actor.id
            or authentication.method
            is not ClinicalAuthenticationMethod.PROVIDER_SESSION
            or not authentication.session_authenticated
        ):
            return TrustAuthorizationDenialCode.PROVIDER_SESSION_REQUIRED
        if not CLINICAL_CONTACT_ASSURANCE_POLICY.is_satisfied(actor):
            return TrustAuthorizationDenialCode.CONTACT_VERIFICATION_REQUIRED
        if not actor.credential.mfa_enabled:
            return TrustAuthorizationDenialCode.MFA_REQUIRED
        mfa_at = authentication.mfa_verified_at
        if (
            mfa_at is None
            or mfa_at.tzinfo is None
            or mfa_at.utcoffset() is None
            or mfa_at > now
        ):
            return TrustAuthorizationDenialCode.MFA_SESSION_ASSURANCE_REQUIRED
        return None

    def _matching_grant(
        self,
        grants: list[ProviderTrustPermissionGrant],
        permission: TrustManagementPermission,
        facility_id: UUID | None,
        now: datetime,
    ) -> TrustAuthorizationDecision:
        expected_scope = scope_for_permission(permission)
        seen_permission = False
        inactive = False
        for grant in grants:
            try:
                grant_permission = TrustManagementPermission(grant.permission)
                grant_scope = TrustPermissionScope(grant.scope_type)
            except (TypeError, ValueError):
                return self._deny(
                    TrustAuthorizationDenialCode.TRUST_PERMISSION_STATE_INVALID,
                    permission,
                    expected_scope,
                )
            if grant_permission is not permission:
                continue
            seen_permission = True
            if (
                grant_scope is not expected_scope
                or (
                    grant_scope is TrustPermissionScope.GLOBAL
                    and grant.facility_id is not None
                )
                or (
                    grant_scope is TrustPermissionScope.FACILITY
                    and grant.facility_id is None
                )
            ):
                return self._deny(
                    TrustAuthorizationDenialCode.TRUST_PERMISSION_STATE_INVALID,
                    permission,
                    expected_scope,
                )
            if (
                grant.revoked_at is not None
                or (grant.valid_from is not None and grant.valid_from > now)
                or (grant.valid_until is not None and grant.valid_until <= now)
            ):
                inactive = True
                continue
            if (
                expected_scope is TrustPermissionScope.FACILITY
                and grant.facility_id != facility_id
            ):
                continue
            return self._allow(permission, expected_scope)
        if inactive:
            return self._deny(
                TrustAuthorizationDenialCode.TRUST_PERMISSION_REVOKED_OR_INACTIVE,
                permission,
                expected_scope,
            )
        if seen_permission and expected_scope is TrustPermissionScope.FACILITY:
            return self._deny(
                TrustAuthorizationDenialCode.TRUST_PERMISSION_SCOPE_MISMATCH,
                permission,
                expected_scope,
            )
        return self._deny(
            TrustAuthorizationDenialCode.TRUST_PERMISSION_REQUIRED,
            permission,
            expected_scope,
        )

    async def _authorize(
        self,
        db: AsyncSession,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        permission: TrustManagementPermission,
        facility_id: UUID | None,
        now: datetime,
    ) -> TrustAuthorizationDecision:
        auth_denial = self._strong_auth(
            await self._actor(db, actor_id), authentication, now
        )
        if auth_denial is not None:
            return self._deny(auth_denial, permission, scope_for_permission(permission))
        return self._matching_grant(
            await self._grants(db, actor_id), permission, facility_id, now
        )

    async def authorize_professional_review(
        self,
        db: AsyncSession,
        *,
        actor_id: UUID,
        target_provider_id: UUID,
        authentication: TrustManagementAuthentication,
        now: datetime | None = None,
    ) -> TrustAuthorizationDecision:
        if actor_id == target_provider_id:
            return self._deny(
                TrustAuthorizationDenialCode.SELF_REVIEW_PROHIBITED,
                TrustManagementPermission.PROFESSIONAL_REVIEW,
                TrustPermissionScope.GLOBAL,
            )
        return await self._authorize(
            db,
            actor_id,
            authentication,
            TrustManagementPermission.PROFESSIONAL_REVIEW,
            None,
            now or datetime.now(timezone.utc),
        )

    async def authorize_professional_self_submission(
        self,
        db: AsyncSession,
        *,
        actor_id: UUID,
        target_provider_id: UUID,
        authentication: TrustManagementAuthentication,
        now: datetime | None = None,
    ) -> TrustAuthorizationDecision:
        if actor_id != target_provider_id:
            return self._deny(TrustAuthorizationDenialCode.TARGET_SCOPE_INVALID)
        moment = now or datetime.now(timezone.utc)
        denial = self._strong_auth(
            await self._actor(db, actor_id), authentication, moment
        )
        return (
            self._deny(denial)
            if denial
            else TrustAuthorizationDecision(True, None, None, None)
        )

    async def authorize_facility_review(
        self,
        db: AsyncSession,
        *,
        actor_id: UUID,
        target_facility_id: UUID,
        authentication: TrustManagementAuthentication,
        now: datetime | None = None,
    ) -> TrustAuthorizationDecision:
        return await self._authorize(
            db,
            actor_id,
            authentication,
            TrustManagementPermission.FACILITY_REVIEW,
            target_facility_id,
            now or datetime.now(timezone.utc),
        )

    async def authorize_affiliation_management(
        self,
        db: AsyncSession,
        *,
        actor_id: UUID,
        target_affiliation_id: UUID,
        authentication: TrustManagementAuthentication,
        now: datetime | None = None,
    ) -> TrustAuthorizationDecision:
        result = await db.execute(
            select(ProviderHospitalAffiliation).where(
                ProviderHospitalAffiliation.id == target_affiliation_id
            )
        )
        target = result.scalar_one_or_none()
        if target is None:
            return self._deny(
                TrustAuthorizationDenialCode.TARGET_SCOPE_INVALID,
                TrustManagementPermission.AFFILIATION_MANAGE,
                TrustPermissionScope.FACILITY,
            )
        if target.provider_id == actor_id:
            return self._deny(
                TrustAuthorizationDenialCode.SELF_AFFILIATION_MANAGEMENT_PROHIBITED,
                TrustManagementPermission.AFFILIATION_MANAGE,
                TrustPermissionScope.FACILITY,
            )
        return await self._authorize(
            db,
            actor_id,
            authentication,
            TrustManagementPermission.AFFILIATION_MANAGE,
            target.hospital_id,
            now or datetime.now(timezone.utc),
        )

    async def authorize_trust_permission_management(
        self,
        db: AsyncSession,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        now: datetime | None = None,
    ) -> TrustAuthorizationDecision:
        return await self._authorize(
            db,
            actor_id,
            authentication,
            TrustManagementPermission.TRUST_PERMISSION_MANAGE,
            None,
            now or datetime.now(timezone.utc),
        )
