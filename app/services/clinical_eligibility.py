"""Server-authoritative provider clinical-eligibility evaluation.

This service intentionally is not wired into patient-facing routes in the
provider-trust-primitives slice.  Its inputs exclude bearer tokens and other
secrets, and it reloads current trust state from the authoritative database on
each evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_break_glass_mfa_max_age_seconds
from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderIdentity,
    VerificationSourceFailureReason,
)
from app.security.provider_capabilities import ClinicalCapability, capability_is_granted

MAX_DELEGATED_TRUST_STALENESS = timedelta(seconds=60)


class ClinicalEligibilityMode(str, Enum):
    INTERACTIVE_CLINICAL = "INTERACTIVE_CLINICAL"
    DELEGATED_CLINICAL_WORKFLOW = "DELEGATED_CLINICAL_WORKFLOW"


class ClinicalAuthenticationMethod(str, Enum):
    PROVIDER_SESSION = "PROVIDER_SESSION"
    BASIC = "BASIC"


class ClinicalEligibilityDenialCode(str, Enum):
    PROVIDER_ACCOUNT_INACTIVE = "PROVIDER_ACCOUNT_INACTIVE"
    PROVIDER_CREDENTIAL_INACTIVE = "PROVIDER_CREDENTIAL_INACTIVE"
    CONTACT_VERIFICATION_REQUIRED = "CONTACT_VERIFICATION_REQUIRED"
    CONTACT_ASSURANCE_POLICY_UNDEFINED = "CONTACT_ASSURANCE_POLICY_UNDEFINED"
    PROFESSIONAL_VERIFICATION_REQUIRED = "PROFESSIONAL_VERIFICATION_REQUIRED"
    PROFESSIONAL_VERIFICATION_PENDING = "PROFESSIONAL_VERIFICATION_PENDING"
    PROFESSIONAL_VERIFICATION_REJECTED = "PROFESSIONAL_VERIFICATION_REJECTED"
    PROFESSIONAL_SUSPENDED = "PROFESSIONAL_SUSPENDED"
    PROFESSIONAL_REVOKED = "PROFESSIONAL_REVOKED"
    PROFESSIONAL_EXPIRED = "PROFESSIONAL_EXPIRED"
    PROFESSIONAL_RECHECK_NOT_ELIGIBLE = "PROFESSIONAL_RECHECK_NOT_ELIGIBLE"
    PROFESSIONAL_VERIFICATION_STALE = "PROFESSIONAL_VERIFICATION_STALE"
    FACILITY_NOT_VERIFIED = "FACILITY_NOT_VERIFIED"
    FACILITY_RECHECK_REQUIRED = "FACILITY_RECHECK_REQUIRED"
    FACILITY_SUSPENDED = "FACILITY_SUSPENDED"
    FACILITY_CLOSED = "FACILITY_CLOSED"
    AFFILIATION_NOT_ACTIVE = "AFFILIATION_NOT_ACTIVE"
    AFFILIATION_NOT_YET_VALID = "AFFILIATION_NOT_YET_VALID"
    AFFILIATION_EXPIRED = "AFFILIATION_EXPIRED"
    AFFILIATION_SUSPENDED = "AFFILIATION_SUSPENDED"
    AFFILIATION_REVOKED = "AFFILIATION_REVOKED"
    AFFILIATION_LEFT = "AFFILIATION_LEFT"
    CLINICAL_CAPABILITY_NOT_GRANTED = "CLINICAL_CAPABILITY_NOT_GRANTED"
    CLINICAL_SESSION_REQUIRED = "CLINICAL_SESSION_REQUIRED"
    CLINICAL_MFA_ENROLLMENT_REQUIRED = "CLINICAL_MFA_ENROLLMENT_REQUIRED"
    CLINICAL_MFA_REQUIRED = "CLINICAL_MFA_REQUIRED"
    RECENT_MFA_REQUIRED = "RECENT_MFA_REQUIRED"
    DELEGATED_WORKFLOW_BINDING_INVALID = "DELEGATED_WORKFLOW_BINDING_INVALID"
    DELEGATED_INITIATION_ASSURANCE_REQUIRED = "DELEGATED_INITIATION_ASSURANCE_REQUIRED"
    DELEGATED_INITIATION_ASSURANCE_INVALID = "DELEGATED_INITIATION_ASSURANCE_INVALID"
    CLINICAL_ELIGIBILITY_DECISION_EXPIRED = "CLINICAL_ELIGIBILITY_DECISION_EXPIRED"
    TRUST_STATE_INTEGRITY_FAILURE = "TRUST_STATE_INTEGRITY_FAILURE"


class ClinicalEligibilityUnavailable(RuntimeError):
    """Raised when current authoritative trust state cannot be loaded."""


@dataclass(frozen=True, slots=True)
class ContactAssurancePolicy:
    """An approved, explicit contact-assurance policy supplied by the caller."""

    require_email_verified: bool
    require_phone_verified: bool
    version: str

    def is_satisfied(self, provider: ProviderIdentity) -> bool:
        if not self.version or not (
            self.require_email_verified or self.require_phone_verified
        ):
            return False
        return (
            not self.require_email_verified or provider.email_verified_at is not None
        ) and (
            not self.require_phone_verified or provider.phone_verified_at is not None
        )


@dataclass(frozen=True, slots=True)
class InteractiveClinicalAuthentication:
    """Non-secret proof of a current bound provider session."""

    provider_id: UUID
    hospital_id: UUID
    method: ClinicalAuthenticationMethod
    session_authenticated: bool
    mfa_verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class DelegatedInitiationAssurance:
    """Non-secret T0 provenance for one delegated workflow continuation."""

    initiated_by_provider_id: UUID
    initiated_hospital_id: UUID
    initiated_at: datetime
    authentication_method: ClinicalAuthenticationMethod
    mfa_verified_at: datetime | None
    assurance_policy_version: str
    workflow_id: UUID | None
    consent_request_id: UUID | None
    required_capability: ClinicalCapability
    workflow_authorization_current: bool


@dataclass(frozen=True, slots=True)
class ClinicalEligibilityResult:
    """Value-free eligibility outcome; never contains source or session data."""

    allowed: bool
    mode: ClinicalEligibilityMode
    capability: ClinicalCapability
    provider_id: UUID
    hospital_id: UUID
    affiliation_id: UUID | None
    checked_at: datetime
    decision_valid_until: datetime | None
    professional_status: ProfessionalVerificationStatus | None
    facility_status: FacilityVerificationStatus | None
    affiliation_status: AffiliationTrustStatus | None
    professional_grace_active: bool
    denial_code: ClinicalEligibilityDenialCode | None
    policy_version: str | None


@dataclass(frozen=True, slots=True)
class _CurrentTrust:
    provider: ProviderIdentity
    hospital: HospitalRegistry | None
    affiliation: object | None


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def _enum(value: str, enum_type: type[Enum]) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid trust state") from exc


class ClinicalEligibilityService:
    """Evaluate current clinical trust without changing route authorization."""

    def __init__(
        self,
        *,
        contact_assurance_policy: ContactAssurancePolicy | None = None,
        recent_mfa_max_age_seconds: Callable[
            [], int
        ] = get_break_glass_mfa_max_age_seconds,
    ) -> None:
        self._contact_assurance_policy = contact_assurance_policy
        self._recent_mfa_max_age_seconds = recent_mfa_max_age_seconds

    async def evaluate_interactive(
        self,
        db: AsyncSession,
        provider: ProviderIdentity,
        authentication: InteractiveClinicalAuthentication,
        capability: ClinicalCapability,
        *,
        now: datetime | None = None,
    ) -> ClinicalEligibilityResult:
        """Evaluate a live interactive clinical request from current DB state."""

        checked_at = self._checked_at(now)
        if not isinstance(capability, ClinicalCapability):
            raise ValueError("capability must be a server-owned ClinicalCapability")
        trust = await self._load_current_trust(
            db, provider.id, authentication.hospital_id
        )
        result = self._evaluate_current_trust(
            trust,
            authentication.hospital_id,
            capability,
            checked_at,
            ClinicalEligibilityMode.INTERACTIVE_CLINICAL,
        )
        if not result.allowed:
            return result
        if (
            authentication.method is not ClinicalAuthenticationMethod.PROVIDER_SESSION
            or not authentication.session_authenticated
            or authentication.provider_id != result.provider_id
        ):
            return self._deny(
                result, ClinicalEligibilityDenialCode.CLINICAL_SESSION_REQUIRED
            )
        if not trust.provider.credential or not trust.provider.credential.mfa_enabled:
            return self._deny(
                result, ClinicalEligibilityDenialCode.CLINICAL_MFA_ENROLLMENT_REQUIRED
            )
        try:
            mfa_verified_at = (
                _require_aware(authentication.mfa_verified_at)
                if authentication.mfa_verified_at is not None
                else None
            )
        except ValueError:
            return self._deny(
                result, ClinicalEligibilityDenialCode.TRUST_STATE_INTEGRITY_FAILURE
            )
        if mfa_verified_at is None or mfa_verified_at > checked_at:
            return self._deny(
                result, ClinicalEligibilityDenialCode.CLINICAL_MFA_REQUIRED
            )
        if capability is ClinicalCapability.EMERGENCY_ATTEMPT:
            try:
                max_age = self._recent_mfa_max_age_seconds()
            except Exception as exc:
                raise ClinicalEligibilityUnavailable(
                    "recent MFA policy unavailable"
                ) from exc
            if checked_at - mfa_verified_at > timedelta(seconds=max_age):
                return self._deny(
                    result, ClinicalEligibilityDenialCode.RECENT_MFA_REQUIRED
                )
        return result

    async def evaluate_delegated(
        self,
        db: AsyncSession,
        provider_id: UUID,
        hospital_id: UUID,
        authorization: DelegatedInitiationAssurance | None,
        capability: ClinicalCapability,
        *,
        now: datetime | None = None,
    ) -> ClinicalEligibilityResult:
        """Evaluate one delegated protected step and issue a <=60-second lease."""

        checked_at = self._checked_at(now)
        if not isinstance(capability, ClinicalCapability):
            raise ValueError("capability must be a server-owned ClinicalCapability")
        trust = await self._load_current_trust(db, provider_id, hospital_id)
        result = self._evaluate_current_trust(
            trust,
            hospital_id,
            capability,
            checked_at,
            ClinicalEligibilityMode.DELEGATED_CLINICAL_WORKFLOW,
        )
        if not result.allowed:
            return result
        if authorization is None:
            return self._deny(
                result,
                ClinicalEligibilityDenialCode.DELEGATED_INITIATION_ASSURANCE_REQUIRED,
            )
        try:
            initiated_at = _require_aware(authorization.initiated_at)
            mfa_verified_at = (
                _require_aware(authorization.mfa_verified_at)
                if authorization.mfa_verified_at is not None
                else None
            )
        except ValueError:
            return self._deny(
                result,
                ClinicalEligibilityDenialCode.DELEGATED_INITIATION_ASSURANCE_INVALID,
            )
        if (
            authorization.initiated_by_provider_id != provider_id
            or authorization.initiated_hospital_id != hospital_id
            or authorization.required_capability is not capability
            or authorization.workflow_id is None
            or authorization.consent_request_id is None
            or not authorization.workflow_authorization_current
        ):
            return self._deny(
                result, ClinicalEligibilityDenialCode.DELEGATED_WORKFLOW_BINDING_INVALID
            )
        if (
            authorization.authentication_method
            is not ClinicalAuthenticationMethod.PROVIDER_SESSION
            or mfa_verified_at is None
            or self._contact_assurance_policy is None
            or authorization.assurance_policy_version
            != self._contact_assurance_policy.version
            or initiated_at > checked_at
            or mfa_verified_at > initiated_at
        ):
            return self._deny(
                result,
                ClinicalEligibilityDenialCode.DELEGATED_INITIATION_ASSURANCE_INVALID,
            )
        return self._allow_with_delegated_lease(result, trust, checked_at)

    def decision_is_current(
        self, decision: ClinicalEligibilityResult, *, now: datetime | None = None
    ) -> bool:
        """Return false once a delegated decision lease expires; callers re-evaluate."""

        checked_at = self._checked_at(now)
        return bool(
            decision.allowed
            and decision.decision_valid_until is not None
            and checked_at < decision.decision_valid_until
        )

    def _checked_at(self, now: datetime | None) -> datetime:
        try:
            return _require_aware(now or datetime.now(timezone.utc))
        except ValueError as exc:
            raise ValueError("now must be timezone-aware") from exc

    async def _load_current_trust(
        self, db: AsyncSession, provider_id: UUID, hospital_id: UUID
    ) -> _CurrentTrust:
        try:
            provider_result = await db.execute(
                select(ProviderIdentity)
                .where(ProviderIdentity.id == provider_id)
                .options(
                    selectinload(ProviderIdentity.credential),
                    selectinload(ProviderIdentity.professional_verification),
                    selectinload(ProviderIdentity.affiliations),
                )
            )
            hospital_result = await db.execute(
                select(HospitalRegistry)
                .where(HospitalRegistry.id == hospital_id)
                .options(selectinload(HospitalRegistry.verification))
            )
            provider = provider_result.scalar_one_or_none()
            hospital = hospital_result.scalar_one_or_none()
        except Exception as exc:
            raise ClinicalEligibilityUnavailable(
                "authoritative trust store unavailable"
            ) from exc
        affiliation = next(
            (
                item
                for item in (provider.affiliations if provider is not None else [])
                if item.hospital_id == hospital_id
            ),
            None,
        )
        return _CurrentTrust(
            provider=provider, hospital=hospital, affiliation=affiliation
        )

    def _evaluate_current_trust(
        self,
        trust: _CurrentTrust,
        hospital_id: UUID,
        capability: ClinicalCapability,
        checked_at: datetime,
        mode: ClinicalEligibilityMode,
    ) -> ClinicalEligibilityResult:
        provider = trust.provider
        if provider is None:
            return self._result(
                False,
                mode,
                capability,
                UUID(int=0),
                hospital_id,
                None,
                checked_at,
                None,
                None,
                None,
                None,
                False,
                ClinicalEligibilityDenialCode.PROVIDER_ACCOUNT_INACTIVE,
            )
        blank = self._result(
            False,
            mode,
            capability,
            provider.id,
            hospital_id,
            None,
            checked_at,
            None,
            None,
            None,
            None,
            False,
            None,
        )
        try:
            if not provider.is_active or provider.status != "active":
                return self._deny(
                    blank, ClinicalEligibilityDenialCode.PROVIDER_ACCOUNT_INACTIVE
                )
            if provider.credential is None or not provider.credential.is_active:
                return self._deny(
                    blank, ClinicalEligibilityDenialCode.PROVIDER_CREDENTIAL_INACTIVE
                )
            if self._contact_assurance_policy is None:
                return self._deny(
                    blank,
                    ClinicalEligibilityDenialCode.CONTACT_ASSURANCE_POLICY_UNDEFINED,
                )
            for verified_at in (provider.email_verified_at, provider.phone_verified_at):
                if verified_at is not None:
                    _require_aware(verified_at)
            if not self._contact_assurance_policy.is_satisfied(provider):
                return self._deny(
                    blank, ClinicalEligibilityDenialCode.CONTACT_VERIFICATION_REQUIRED
                )
            professional = provider.professional_verification
            professional_status, grace_active, professional_denial = (
                self._professional_status(professional, checked_at)
            )
            current = self._replace(
                blank,
                professional_status=professional_status,
                professional_grace_active=grace_active,
            )
            if professional_denial is not None:
                return self._deny(current, professional_denial)
            facility_status, facility_denial = self._facility_status(
                trust.hospital, checked_at
            )
            current = self._replace(current, facility_status=facility_status)
            if facility_denial is not None:
                return self._deny(current, facility_denial)
            affiliation_status, affiliation_denial = self._affiliation_status(
                trust.affiliation, checked_at
            )
            current = self._replace(
                current,
                affiliation_id=getattr(trust.affiliation, "id", None),
                affiliation_status=affiliation_status,
            )
            if affiliation_denial is not None:
                return self._deny(current, affiliation_denial)
            if not capability_is_granted(
                getattr(trust.affiliation, "roles", None), capability
            ):
                return self._deny(
                    current,
                    ClinicalEligibilityDenialCode.CLINICAL_CAPABILITY_NOT_GRANTED,
                )
            return self._replace(
                current,
                allowed=True,
                policy_version=self._contact_assurance_policy.version,
            )
        except ValueError:
            return self._deny(
                blank, ClinicalEligibilityDenialCode.TRUST_STATE_INTEGRITY_FAILURE
            )

    def _professional_status(
        self, verification: ProfessionalVerification | None, now: datetime
    ) -> tuple[
        ProfessionalVerificationStatus | None,
        bool,
        ClinicalEligibilityDenialCode | None,
    ]:
        if verification is None:
            return (
                None,
                False,
                ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_REQUIRED,
            )
        status = _enum(verification.status, ProfessionalVerificationStatus)
        if (
            status is ProfessionalVerificationStatus.PENDING_REVIEW
            or status is ProfessionalVerificationStatus.NOT_SUBMITTED
        ):
            return (
                status,
                False,
                ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_PENDING
                if status is ProfessionalVerificationStatus.PENDING_REVIEW
                else ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_REQUIRED,
            )
        if status is ProfessionalVerificationStatus.REJECTED:
            return (
                status,
                False,
                ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_REJECTED,
            )
        if status is ProfessionalVerificationStatus.SUSPENDED:
            return status, False, ClinicalEligibilityDenialCode.PROFESSIONAL_SUSPENDED
        if status is ProfessionalVerificationStatus.REVOKED:
            return status, False, ClinicalEligibilityDenialCode.PROFESSIONAL_REVOKED
        if status is ProfessionalVerificationStatus.EXPIRED:
            return status, False, ClinicalEligibilityDenialCode.PROFESSIONAL_EXPIRED
        if status is ProfessionalVerificationStatus.VERIFICATION_STALE:
            return (
                status,
                False,
                ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_STALE,
            )
        valid_until = verification.registration_valid_until
        if valid_until is not None and _require_aware(valid_until) <= now:
            return status, False, ClinicalEligibilityDenialCode.PROFESSIONAL_EXPIRED
        if (
            verification.registration_valid_from is not None
            and _require_aware(verification.registration_valid_from) > now
        ):
            return (
                status,
                False,
                ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_STALE,
            )
        if status is ProfessionalVerificationStatus.VERIFIED:
            if verification.verified_at is None:
                return (
                    status,
                    False,
                    ClinicalEligibilityDenialCode.TRUST_STATE_INTEGRITY_FAILURE,
                )
            _require_aware(verification.verified_at)
            if (
                verification.next_review_at is not None
                and _require_aware(verification.next_review_at) <= now
            ):
                return (
                    status,
                    False,
                    ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_STALE,
                )
            return status, False, None
        if status is ProfessionalVerificationStatus.RECHECK_DUE:
            grace_expires_at = verification.grace_expires_at
            if grace_expires_at is None:
                return (
                    status,
                    False,
                    ClinicalEligibilityDenialCode.PROFESSIONAL_RECHECK_NOT_ELIGIBLE,
                )
            grace_expires_at = _require_aware(grace_expires_at)
            if grace_expires_at <= now:
                return (
                    status,
                    False,
                    ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_STALE,
                )
            if (
                verification.previous_verification_valid
                and verification.recheck_attempted_at is not None
                and verification.recheck_failure_reason
                == VerificationSourceFailureReason.SOURCE_UNAVAILABLE.value
                and verification.authoritative_adverse_signal_at is None
            ):
                _require_aware(verification.recheck_attempted_at)
                return status, True, None
            return (
                status,
                False,
                ClinicalEligibilityDenialCode.PROFESSIONAL_RECHECK_NOT_ELIGIBLE,
            )
        raise ValueError("unknown professional state")

    def _facility_status(
        self, hospital: HospitalRegistry | None, now: datetime
    ) -> tuple[FacilityVerificationStatus | None, ClinicalEligibilityDenialCode | None]:
        if hospital is None or hospital.verification is None:
            return None, ClinicalEligibilityDenialCode.FACILITY_NOT_VERIFIED
        status = _enum(hospital.verification.status, FacilityVerificationStatus)
        if not hospital.is_active or status is FacilityVerificationStatus.CLOSED:
            return status, ClinicalEligibilityDenialCode.FACILITY_CLOSED
        if status is FacilityVerificationStatus.SUSPENDED:
            return status, ClinicalEligibilityDenialCode.FACILITY_SUSPENDED
        if status is FacilityVerificationStatus.RECHECK_REQUIRED:
            return status, ClinicalEligibilityDenialCode.FACILITY_RECHECK_REQUIRED
        if status is not FacilityVerificationStatus.VERIFIED:
            return status, ClinicalEligibilityDenialCode.FACILITY_NOT_VERIFIED
        if hospital.verification.verified_at is None:
            return status, ClinicalEligibilityDenialCode.TRUST_STATE_INTEGRITY_FAILURE
        _require_aware(hospital.verification.verified_at)
        if (
            hospital.verification.next_review_at is not None
            and _require_aware(hospital.verification.next_review_at) <= now
        ):
            return status, ClinicalEligibilityDenialCode.FACILITY_RECHECK_REQUIRED
        return status, None

    def _affiliation_status(
        self, affiliation: object | None, now: datetime
    ) -> tuple[AffiliationTrustStatus | None, ClinicalEligibilityDenialCode | None]:
        if affiliation is None:
            return None, ClinicalEligibilityDenialCode.AFFILIATION_NOT_ACTIVE
        status = _enum(
            getattr(affiliation, "trust_status", None), AffiliationTrustStatus
        )
        if status is AffiliationTrustStatus.SUSPENDED:
            return status, ClinicalEligibilityDenialCode.AFFILIATION_SUSPENDED
        if status is AffiliationTrustStatus.REVOKED:
            return status, ClinicalEligibilityDenialCode.AFFILIATION_REVOKED
        if status is AffiliationTrustStatus.LEFT:
            return status, ClinicalEligibilityDenialCode.AFFILIATION_LEFT
        if status is AffiliationTrustStatus.EXPIRED:
            return status, ClinicalEligibilityDenialCode.AFFILIATION_EXPIRED
        if status is not AffiliationTrustStatus.ACTIVE:
            return status, ClinicalEligibilityDenialCode.AFFILIATION_NOT_ACTIVE
        valid_from = getattr(affiliation, "valid_from", None)
        valid_until = getattr(affiliation, "valid_until", None)
        if valid_from is not None and _require_aware(valid_from) > now:
            return status, ClinicalEligibilityDenialCode.AFFILIATION_NOT_YET_VALID
        if valid_until is not None and _require_aware(valid_until) <= now:
            return status, ClinicalEligibilityDenialCode.AFFILIATION_EXPIRED
        # The explicit trust_status is authoritative. is_active is legacy
        # compatibility state and cannot grant clinical authority by itself.
        return status, None

    def _allow_with_delegated_lease(
        self, result: ClinicalEligibilityResult, trust: _CurrentTrust, now: datetime
    ) -> ClinicalEligibilityResult:
        boundaries = [now + MAX_DELEGATED_TRUST_STALENESS]
        verification = trust.provider.professional_verification
        if verification is not None:
            for value in (
                verification.registration_valid_until,
                verification.grace_expires_at
                if result.professional_grace_active
                else None,
                verification.next_review_at,
            ):
                if value is not None:
                    boundary = _require_aware(value)
                    if boundary > now:
                        boundaries.append(boundary)
        if trust.hospital is not None and trust.hospital.verification is not None:
            if trust.hospital.verification.next_review_at is not None:
                boundary = _require_aware(trust.hospital.verification.next_review_at)
                if boundary > now:
                    boundaries.append(boundary)
        valid_until = getattr(trust.affiliation, "valid_until", None)
        if valid_until is not None:
            boundary = _require_aware(valid_until)
            if boundary > now:
                boundaries.append(boundary)
        return self._replace(result, decision_valid_until=min(boundaries))

    @staticmethod
    def _result(
        allowed: bool,
        mode: ClinicalEligibilityMode,
        capability: ClinicalCapability,
        provider_id: UUID,
        hospital_id: UUID,
        affiliation_id: UUID | None,
        checked_at: datetime,
        decision_valid_until: datetime | None,
        professional_status: ProfessionalVerificationStatus | None,
        facility_status: FacilityVerificationStatus | None,
        affiliation_status: AffiliationTrustStatus | None,
        professional_grace_active: bool,
        denial_code: ClinicalEligibilityDenialCode | None,
        policy_version: str | None = None,
    ) -> ClinicalEligibilityResult:
        return ClinicalEligibilityResult(
            allowed,
            mode,
            capability,
            provider_id,
            hospital_id,
            affiliation_id,
            checked_at,
            decision_valid_until,
            professional_status,
            facility_status,
            affiliation_status,
            professional_grace_active,
            denial_code,
            policy_version,
        )

    @staticmethod
    def _replace(
        result: ClinicalEligibilityResult, **changes: object
    ) -> ClinicalEligibilityResult:
        return replace(result, **changes)

    def _deny(
        self, result: ClinicalEligibilityResult, code: ClinicalEligibilityDenialCode
    ) -> ClinicalEligibilityResult:
        return self._replace(
            result, allowed=False, decision_valid_until=None, denial_code=code
        )
