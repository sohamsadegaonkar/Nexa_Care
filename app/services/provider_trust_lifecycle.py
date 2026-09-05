"""Pure lifecycle policy for independent provider-trust records.

This module intentionally has no persistence, authorization, HTTP, audit-outbox,
or idempotency dependency.  It turns trusted, server-owned evidence into an
immutable transition plan.  A later transactional layer owns locks, expected
version enforcement, durable auditing, and applying the returned updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerificationStatus,
    ProfessionalVerificationStatus,
    VerificationSourceFailureReason,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent


class LifecyclePolicyError(ValueError):
    """A deterministic, non-sensitive policy denial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProfessionalTransitionCommand(str, Enum):
    SUBMIT = "SUBMIT"
    VERIFY = "VERIFY"
    REJECT = "REJECT"
    SUSPEND = "SUSPEND"
    RESTORE = "RESTORE"
    MARK_RECHECK_DUE = "MARK_RECHECK_DUE"
    COMPLETE_RECHECK = "COMPLETE_RECHECK"
    MARK_STALE = "MARK_STALE"
    REVOKE = "REVOKE"
    EXPIRE = "EXPIRE"
    CANCEL_RECHECK_GRACE = "CANCEL_RECHECK_GRACE"


class FacilityTransitionCommand(str, Enum):
    SUBMIT = "SUBMIT"
    VERIFY = "VERIFY"
    REJECT = "REJECT"
    SUSPEND = "SUSPEND"
    RESTORE = "RESTORE"
    MARK_RECHECK_REQUIRED = "MARK_RECHECK_REQUIRED"
    COMPLETE_RECHECK = "COMPLETE_RECHECK"
    CLOSE = "CLOSE"


class AffiliationTransitionCommand(str, Enum):
    ACTIVATE = "ACTIVATE"
    SUSPEND = "SUSPEND"
    RESTORE = "RESTORE"
    REVOKE = "REVOKE"
    EXPIRE = "EXPIRE"
    LEAVE = "LEAVE"


@dataclass(frozen=True)
class ProfessionalTransitionFacts:
    """Evidence produced by trusted registration, verification, or review work.

    This type is deliberately not a request schema.  It represents facts
    collected and normalized by a future authorized application layer.
    """

    registration_authority_code: str | None = None
    registration_number_normalized: str | None = None
    verification_method: str | None = None
    verification_source: str | None = None
    verification_reference: str | None = None
    identity_binding_method: str | None = None
    identity_binding_status: str | None = None
    registration_valid_from: datetime | None = None
    registration_valid_until: datetime | None = None
    next_review_at: datetime | None = None
    reviewer_id: str | None = None
    decision_reason_code: str | None = None
    recheck_attempted_at: datetime | None = None
    recheck_failure_reason: VerificationSourceFailureReason | None = None
    grace_expires_at: datetime | None = None
    previous_verification_valid: bool | None = None
    authoritative_adverse_signal_at: datetime | None = None


@dataclass(frozen=True)
class FacilityTransitionFacts:
    """Trusted facility-verification or review evidence, never request data."""

    verification_method: str | None = None
    verification_source: str | None = None
    verification_reference: str | None = None
    next_review_at: datetime | None = None
    reviewer_id: str | None = None
    decision_reason_code: str | None = None


@dataclass(frozen=True)
class AffiliationTransitionFacts:
    """Trusted relationship facts.  Roles and capabilities are intentionally absent."""

    decision_reason_code: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True)
class FieldUpdate:
    field: str
    value: object


@dataclass(frozen=True)
class LifecycleTransitionPlan:
    """Immutable policy output; no plan performs a write by itself."""

    old_state: str
    new_state: str
    command: str
    event_type: ProviderTrustAuditEvent
    updates: tuple[FieldUpdate, ...]
    clears: frozenset[str]
    terminal: bool
    expected_version: int
    next_version: int


_MAX_RECHECK_GRACE = timedelta(hours=24)


def _require_version(version: int) -> None:
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise LifecyclePolicyError("LIFECYCLE_VERSION_INVALID")


def _require_now(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise LifecyclePolicyError("LIFECYCLE_TIME_INVALID")


def _require_text(value: str | None, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise LifecyclePolicyError(f"LIFECYCLE_{field.upper()}_REQUIRED")
    return value.strip()


def _require_aware(value: datetime | None, field: str) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise LifecyclePolicyError(f"LIFECYCLE_{field.upper()}_REQUIRED")
    return value


def _state(value: object, enum_type: type[Enum]) -> Enum:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        raise LifecyclePolicyError("LIFECYCLE_STATE_INVALID") from exc


def _plan(
    *,
    old_state: Enum,
    new_state: Enum,
    command: Enum,
    event_type: ProviderTrustAuditEvent,
    updates: tuple[FieldUpdate, ...],
    clears: frozenset[str] = frozenset(),
    terminal: bool = False,
    current_version: int,
    allow_same_state: bool = False,
) -> LifecycleTransitionPlan:
    _require_version(current_version)
    if old_state.value == new_state.value and not allow_same_state:
        raise LifecyclePolicyError("LIFECYCLE_SAME_STATE_FORBIDDEN")
    return LifecycleTransitionPlan(
        old_state=old_state.value,
        new_state=new_state.value,
        command=command.value,
        event_type=event_type,
        updates=updates + (FieldUpdate("version", current_version + 1),),
        clears=clears,
        terminal=terminal,
        expected_version=current_version,
        next_version=current_version + 1,
    )


def _professional_evidence(
    facts: ProfessionalTransitionFacts, now: datetime
) -> tuple[FieldUpdate, ...]:
    authority = _require_text(
        facts.registration_authority_code, "registration_authority"
    )
    number = _require_text(facts.registration_number_normalized, "registration_number")
    method = _require_text(facts.verification_method, "verification_method")
    source = _require_text(facts.verification_source, "verification_source")
    reference = _require_text(facts.verification_reference, "verification_reference")
    binding_method = _require_text(
        facts.identity_binding_method, "identity_binding_method"
    )
    binding_status = _require_text(
        facts.identity_binding_status, "identity_binding_status"
    )
    reviewer = _require_text(facts.reviewer_id, "reviewer")
    valid_from = facts.registration_valid_from
    valid_until = facts.registration_valid_until
    if valid_from is not None:
        _require_aware(valid_from, "registration_valid_from")
    if valid_until is not None:
        valid_until = _require_aware(valid_until, "registration_valid_until")
        if valid_until <= now:
            raise LifecyclePolicyError("LIFECYCLE_REGISTRATION_VALIDITY_INVALID")
    if valid_from is not None and valid_until is not None and valid_from > valid_until:
        raise LifecyclePolicyError("LIFECYCLE_REGISTRATION_VALIDITY_INVALID")
    if (
        facts.next_review_at is not None
        and _require_aware(facts.next_review_at, "next_review_at") <= now
    ):
        raise LifecyclePolicyError("LIFECYCLE_NEXT_REVIEW_INVALID")
    return (
        FieldUpdate("registration_authority_code", authority),
        FieldUpdate("registration_number_normalized", number),
        FieldUpdate("verification_method", method),
        FieldUpdate("verification_source", source),
        FieldUpdate("verification_reference", reference),
        FieldUpdate("identity_binding_method", binding_method),
        FieldUpdate("identity_binding_status", binding_status),
        FieldUpdate("registration_valid_from", valid_from),
        FieldUpdate("registration_valid_until", valid_until),
        FieldUpdate("verified_at", now),
        FieldUpdate("last_checked_at", now),
        FieldUpdate("next_review_at", facts.next_review_at),
        FieldUpdate("reviewer_id", reviewer),
        FieldUpdate("previous_verification_valid", True),
    )


def _professional_submission_identity(
    facts: ProfessionalTransitionFacts,
) -> tuple[str, str]:
    """Accept only registration identity at initial submission.

    Verification, reviewer, decision, recheck, and grace fields must be
    introduced by their distinct trusted lifecycle commands, never bundled
    into a registration submission.
    """

    forbidden = (
        facts.verification_method,
        facts.verification_source,
        facts.verification_reference,
        facts.identity_binding_method,
        facts.identity_binding_status,
        facts.registration_valid_from,
        facts.registration_valid_until,
        facts.next_review_at,
        facts.reviewer_id,
        facts.decision_reason_code,
        facts.recheck_attempted_at,
        facts.recheck_failure_reason,
        facts.grace_expires_at,
        facts.authoritative_adverse_signal_at,
    )
    if any(
        value is not None for value in forbidden
    ) or facts.previous_verification_valid not in (
        None,
        False,
    ):
        raise LifecyclePolicyError("LIFECYCLE_SUBMISSION_FACTS_INVALID")
    return (
        _require_text(facts.registration_authority_code, "registration_authority"),
        _require_text(facts.registration_number_normalized, "registration_number"),
    )


def plan_professional_transition(
    current_state: ProfessionalVerificationStatus | str,
    command: ProfessionalTransitionCommand,
    facts: ProfessionalTransitionFacts,
    now: datetime,
    *,
    current_version: int,
) -> LifecycleTransitionPlan:
    """Plan one allowed professional transition without mutating anything."""

    _require_now(now)
    state = _state(current_state, ProfessionalVerificationStatus)
    if not isinstance(command, ProfessionalTransitionCommand):
        raise LifecyclePolicyError("LIFECYCLE_COMMAND_INVALID")
    if (
        state is ProfessionalVerificationStatus.NOT_SUBMITTED
        and command is ProfessionalTransitionCommand.SUBMIT
    ):
        authority, number = _professional_submission_identity(facts)
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.PENDING_REVIEW,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_SUBMITTED,
            updates=(
                FieldUpdate("registration_authority_code", authority),
                FieldUpdate("registration_number_normalized", number),
                FieldUpdate(
                    "status", ProfessionalVerificationStatus.PENDING_REVIEW.value
                ),
                FieldUpdate("previous_verification_valid", False),
            ),
            clears=frozenset(
                {
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
                }
            ),
            current_version=current_version,
        )
    if (
        state is ProfessionalVerificationStatus.PENDING_REVIEW
        and command is ProfessionalTransitionCommand.VERIFY
    ):
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.VERIFIED,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFIED,
            updates=(
                FieldUpdate("status", ProfessionalVerificationStatus.VERIFIED.value),
            )
            + _professional_evidence(facts, now),
            clears=frozenset(
                {
                    "grace_expires_at",
                    "recheck_attempted_at",
                    "recheck_failure_reason",
                    "authoritative_adverse_signal_at",
                    "decision_reason_code",
                }
            ),
            current_version=current_version,
        )
    if (
        state is ProfessionalVerificationStatus.PENDING_REVIEW
        and command is ProfessionalTransitionCommand.REJECT
    ):
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.REJECTED,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_REJECTED,
            updates=(
                FieldUpdate("status", ProfessionalVerificationStatus.REJECTED.value),
                FieldUpdate("decision_reason_code", reason),
                FieldUpdate("previous_verification_valid", False),
            ),
            clears=frozenset(
                {
                    "verified_at",
                    "last_checked_at",
                    "next_review_at",
                    "grace_expires_at",
                    "recheck_attempted_at",
                    "recheck_failure_reason",
                    "authoritative_adverse_signal_at",
                    "reviewer_id",
                }
            ),
            terminal=True,
            current_version=current_version,
        )
    if (
        state is ProfessionalVerificationStatus.VERIFIED
        and command is ProfessionalTransitionCommand.MARK_RECHECK_DUE
    ):
        attempted = _require_aware(facts.recheck_attempted_at, "recheck_attempted_at")
        if attempted > now or facts.previous_verification_valid is not True:
            raise LifecyclePolicyError("LIFECYCLE_RECHECK_EVIDENCE_INVALID")
        if facts.authoritative_adverse_signal_at is not None:
            _require_aware(
                facts.authoritative_adverse_signal_at, "authoritative_adverse_signal_at"
            )
            raise LifecyclePolicyError("LIFECYCLE_RECHECK_ADVERSE_SIGNAL")
        failure = facts.recheck_failure_reason
        grace = facts.grace_expires_at
        if failure is VerificationSourceFailureReason.SOURCE_UNAVAILABLE:
            grace = _require_aware(grace, "grace_expires_at")
            if grace <= now or grace > now + _MAX_RECHECK_GRACE:
                raise LifecyclePolicyError("LIFECYCLE_RECHECK_GRACE_INVALID")
            if (
                facts.registration_valid_until is not None
                and grace > facts.registration_valid_until
            ):
                raise LifecyclePolicyError("LIFECYCLE_RECHECK_GRACE_INVALID")
        elif grace is not None or failure is not None:
            raise LifecyclePolicyError("LIFECYCLE_RECHECK_EVIDENCE_INVALID")
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.RECHECK_DUE,
            command=command,
            event_type=(
                ProviderTrustAuditEvent.PROVIDER_VERIFICATION_SOURCE_UNAVAILABLE
                if failure is VerificationSourceFailureReason.SOURCE_UNAVAILABLE
                else ProviderTrustAuditEvent.PROVIDER_REVERIFICATION_PERFORMED
            ),
            updates=(
                FieldUpdate("status", ProfessionalVerificationStatus.RECHECK_DUE.value),
                FieldUpdate("recheck_attempted_at", attempted),
                FieldUpdate(
                    "recheck_failure_reason", failure.value if failure else None
                ),
                FieldUpdate("grace_expires_at", grace),
                FieldUpdate("previous_verification_valid", True),
            ),
            current_version=current_version,
        )
    if (
        state is ProfessionalVerificationStatus.RECHECK_DUE
        and command is ProfessionalTransitionCommand.COMPLETE_RECHECK
    ):
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.VERIFIED,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_REVERIFICATION_PERFORMED,
            updates=(
                FieldUpdate("status", ProfessionalVerificationStatus.VERIFIED.value),
            )
            + _professional_evidence(facts, now),
            clears=frozenset(
                {
                    "grace_expires_at",
                    "recheck_attempted_at",
                    "recheck_failure_reason",
                    "authoritative_adverse_signal_at",
                    "decision_reason_code",
                }
            ),
            current_version=current_version,
        )
    if (
        state is ProfessionalVerificationStatus.RECHECK_DUE
        and command is ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE
    ):
        attempted = _require_aware(facts.recheck_attempted_at, "recheck_attempted_at")
        if attempted > now or facts.previous_verification_valid is not True:
            raise LifecyclePolicyError("LIFECYCLE_RECHECK_EVIDENCE_INVALID")
        failure = facts.recheck_failure_reason
        if failure not in {
            VerificationSourceFailureReason.SOURCE_RESPONSE_INVALID,
            VerificationSourceFailureReason.SOURCE_NOT_FOUND,
            VerificationSourceFailureReason.REVIEW_REQUIRED,
        }:
            raise LifecyclePolicyError("LIFECYCLE_RECHECK_EVIDENCE_INVALID")
        if facts.grace_expires_at is not None:
            raise LifecyclePolicyError("LIFECYCLE_RECHECK_EVIDENCE_INVALID")
        adverse = facts.authoritative_adverse_signal_at
        if adverse is not None:
            _require_aware(adverse, "authoritative_adverse_signal_at")
            if adverse > now:
                raise LifecyclePolicyError("LIFECYCLE_RECHECK_ADVERSE_SIGNAL")
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.RECHECK_DUE,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_RECHECK_GRACE_CANCELLED,
            updates=(
                FieldUpdate("status", ProfessionalVerificationStatus.RECHECK_DUE.value),
                FieldUpdate("recheck_attempted_at", attempted),
                FieldUpdate("recheck_failure_reason", failure.value),
                FieldUpdate("authoritative_adverse_signal_at", adverse),
                FieldUpdate("previous_verification_valid", True),
            ),
            clears=frozenset({"grace_expires_at"}),
            current_version=current_version,
            allow_same_state=True,
        )
    if (
        state is ProfessionalVerificationStatus.RECHECK_DUE
        and command is ProfessionalTransitionCommand.MARK_STALE
    ):
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.VERIFICATION_STALE,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_STALE,
            updates=(
                FieldUpdate(
                    "status", ProfessionalVerificationStatus.VERIFICATION_STALE.value
                ),
                FieldUpdate("previous_verification_valid", False),
            ),
            clears=frozenset(
                {"grace_expires_at", "recheck_attempted_at", "recheck_failure_reason"}
            ),
            terminal=True,
            current_version=current_version,
        )
    if state in {
        ProfessionalVerificationStatus.VERIFIED,
        ProfessionalVerificationStatus.RECHECK_DUE,
    } and command in {
        ProfessionalTransitionCommand.REVOKE,
        ProfessionalTransitionCommand.EXPIRE,
    }:
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        new_state = (
            ProfessionalVerificationStatus.REVOKED
            if command is ProfessionalTransitionCommand.REVOKE
            else ProfessionalVerificationStatus.EXPIRED
        )
        event = (
            ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_REVOKED
            if command is ProfessionalTransitionCommand.REVOKE
            else ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_VERIFICATION_EXPIRED
        )
        return _plan(
            old_state=state,
            new_state=new_state,
            command=command,
            event_type=event,
            updates=(
                FieldUpdate("status", new_state.value),
                FieldUpdate("decision_reason_code", reason),
                FieldUpdate("previous_verification_valid", False),
            ),
            clears=frozenset(
                {"grace_expires_at", "recheck_attempted_at", "recheck_failure_reason"}
            ),
            terminal=True,
            current_version=current_version,
        )
    if (
        state is ProfessionalVerificationStatus.VERIFIED
        and command is ProfessionalTransitionCommand.SUSPEND
    ):
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.SUSPENDED,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_SUSPENDED,
            updates=(
                FieldUpdate("status", ProfessionalVerificationStatus.SUSPENDED.value),
                FieldUpdate("decision_reason_code", reason),
                FieldUpdate("previous_verification_valid", False),
                FieldUpdate("authoritative_adverse_signal_at", now),
            ),
            clears=frozenset(
                {"grace_expires_at", "recheck_attempted_at", "recheck_failure_reason"}
            ),
            current_version=current_version,
        )
    if (
        state is ProfessionalVerificationStatus.SUSPENDED
        and command is ProfessionalTransitionCommand.RESTORE
    ):
        return _plan(
            old_state=state,
            new_state=ProfessionalVerificationStatus.VERIFIED,
            command=command,
            event_type=ProviderTrustAuditEvent.PROVIDER_PROFESSIONAL_RESTORED,
            updates=(
                FieldUpdate("status", ProfessionalVerificationStatus.VERIFIED.value),
            )
            + _professional_evidence(facts, now),
            clears=frozenset(
                {
                    "grace_expires_at",
                    "recheck_attempted_at",
                    "recheck_failure_reason",
                    "authoritative_adverse_signal_at",
                    "decision_reason_code",
                }
            ),
            current_version=current_version,
        )
    raise LifecyclePolicyError("LIFECYCLE_TRANSITION_NOT_ALLOWED")


def _facility_evidence(
    facts: FacilityTransitionFacts, now: datetime
) -> tuple[FieldUpdate, ...]:
    method = _require_text(facts.verification_method, "verification_method")
    source = _require_text(facts.verification_source, "verification_source")
    reference = _require_text(facts.verification_reference, "verification_reference")
    reviewer = _require_text(facts.reviewer_id, "reviewer")
    if (
        facts.next_review_at is not None
        and _require_aware(facts.next_review_at, "next_review_at") <= now
    ):
        raise LifecyclePolicyError("LIFECYCLE_NEXT_REVIEW_INVALID")
    return (
        FieldUpdate("verification_method", method),
        FieldUpdate("verification_source", source),
        FieldUpdate("verification_reference", reference),
        FieldUpdate("verified_at", now),
        FieldUpdate("last_checked_at", now),
        FieldUpdate("next_review_at", facts.next_review_at),
        FieldUpdate("reviewer_id", reviewer),
    )


def plan_facility_transition(
    current_state: FacilityVerificationStatus | str,
    command: FacilityTransitionCommand,
    facts: FacilityTransitionFacts,
    now: datetime,
    *,
    current_version: int,
) -> LifecycleTransitionPlan:
    """Plan one allowed facility transition; never changes facility resources."""
    _require_now(now)
    state = _state(current_state, FacilityVerificationStatus)
    if not isinstance(command, FacilityTransitionCommand):
        raise LifecyclePolicyError("LIFECYCLE_COMMAND_INVALID")
    if (
        state is FacilityVerificationStatus.DRAFT
        and command is FacilityTransitionCommand.SUBMIT
    ):
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.PENDING_VERIFICATION,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_VERIFICATION_SUBMITTED,
            updates=(
                FieldUpdate(
                    "status", FacilityVerificationStatus.PENDING_VERIFICATION.value
                ),
            ),
            clears=frozenset(
                {
                    "verified_at",
                    "last_checked_at",
                    "next_review_at",
                    "reviewer_id",
                    "decision_reason_code",
                }
            ),
            current_version=current_version,
        )
    if (
        state is FacilityVerificationStatus.PENDING_VERIFICATION
        and command is FacilityTransitionCommand.VERIFY
    ):
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.VERIFIED,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_VERIFIED,
            updates=(FieldUpdate("status", FacilityVerificationStatus.VERIFIED.value),)
            + _facility_evidence(facts, now),
            clears=frozenset({"decision_reason_code"}),
            current_version=current_version,
        )
    if (
        state is FacilityVerificationStatus.PENDING_VERIFICATION
        and command is FacilityTransitionCommand.REJECT
    ):
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.REJECTED,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_REJECTED,
            updates=(
                FieldUpdate("status", FacilityVerificationStatus.REJECTED.value),
                FieldUpdate("decision_reason_code", reason),
            ),
            clears=frozenset(
                {"verified_at", "last_checked_at", "next_review_at", "reviewer_id"}
            ),
            terminal=True,
            current_version=current_version,
        )
    if (
        state is FacilityVerificationStatus.VERIFIED
        and command is FacilityTransitionCommand.MARK_RECHECK_REQUIRED
    ):
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.RECHECK_REQUIRED,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_RECHECK_REQUIRED,
            updates=(
                FieldUpdate(
                    "status", FacilityVerificationStatus.RECHECK_REQUIRED.value
                ),
            ),
            current_version=current_version,
        )
    if (
        state is FacilityVerificationStatus.RECHECK_REQUIRED
        and command is FacilityTransitionCommand.COMPLETE_RECHECK
    ):
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.VERIFIED,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_VERIFIED,
            updates=(FieldUpdate("status", FacilityVerificationStatus.VERIFIED.value),)
            + _facility_evidence(facts, now),
            clears=frozenset({"decision_reason_code"}),
            current_version=current_version,
        )
    if (
        state is FacilityVerificationStatus.VERIFIED
        and command is FacilityTransitionCommand.SUSPEND
    ):
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.SUSPENDED,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_SUSPENDED,
            updates=(
                FieldUpdate("status", FacilityVerificationStatus.SUSPENDED.value),
                FieldUpdate("decision_reason_code", reason),
            ),
            current_version=current_version,
        )
    if (
        state is FacilityVerificationStatus.SUSPENDED
        and command is FacilityTransitionCommand.RESTORE
    ):
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.VERIFIED,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_RESTORED,
            updates=(FieldUpdate("status", FacilityVerificationStatus.VERIFIED.value),)
            + _facility_evidence(facts, now),
            clears=frozenset({"decision_reason_code"}),
            current_version=current_version,
        )
    if (
        state
        in {
            FacilityVerificationStatus.VERIFIED,
            FacilityVerificationStatus.SUSPENDED,
            FacilityVerificationStatus.RECHECK_REQUIRED,
        }
        and command is FacilityTransitionCommand.CLOSE
    ):
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        return _plan(
            old_state=state,
            new_state=FacilityVerificationStatus.CLOSED,
            command=command,
            event_type=ProviderTrustAuditEvent.FACILITY_CLOSED,
            updates=(
                FieldUpdate("status", FacilityVerificationStatus.CLOSED.value),
                FieldUpdate("decision_reason_code", reason),
            ),
            terminal=True,
            current_version=current_version,
        )
    raise LifecyclePolicyError("LIFECYCLE_TRANSITION_NOT_ALLOWED")


def plan_affiliation_transition(
    current_state: AffiliationTrustStatus | str,
    command: AffiliationTransitionCommand,
    facts: AffiliationTransitionFacts,
    now: datetime,
    *,
    current_version: int,
) -> LifecycleTransitionPlan:
    """Plan one exact provider-to-hospital relationship state transition."""
    _require_now(now)
    state = _state(current_state, AffiliationTrustStatus)
    if not isinstance(command, AffiliationTransitionCommand):
        raise LifecyclePolicyError("LIFECYCLE_COMMAND_INVALID")
    if facts.valid_from is not None:
        _require_aware(facts.valid_from, "valid_from")
    if facts.valid_until is not None:
        _require_aware(facts.valid_until, "valid_until")
    if facts.valid_from and facts.valid_until and facts.valid_from > facts.valid_until:
        raise LifecyclePolicyError("LIFECYCLE_AFFILIATION_VALIDITY_INVALID")
    if (
        state is AffiliationTrustStatus.PENDING_ACTIVATION
        and command is AffiliationTransitionCommand.ACTIVATE
    ):
        if facts.valid_from is not None and facts.valid_from > now:
            raise LifecyclePolicyError("LIFECYCLE_AFFILIATION_NOT_CURRENT")
        if facts.valid_until is not None and facts.valid_until <= now:
            raise LifecyclePolicyError("LIFECYCLE_AFFILIATION_VALIDITY_INVALID")
        return _plan(
            old_state=state,
            new_state=AffiliationTrustStatus.ACTIVE,
            command=command,
            event_type=ProviderTrustAuditEvent.AFFILIATION_ACTIVATED,
            updates=(
                FieldUpdate("trust_status", AffiliationTrustStatus.ACTIVE.value),
                FieldUpdate("valid_from", facts.valid_from),
                FieldUpdate("valid_until", facts.valid_until),
            ),
            current_version=current_version,
        )
    if (
        state is AffiliationTrustStatus.ACTIVE
        and command is AffiliationTransitionCommand.SUSPEND
    ):
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        return _plan(
            old_state=state,
            new_state=AffiliationTrustStatus.SUSPENDED,
            command=command,
            event_type=ProviderTrustAuditEvent.AFFILIATION_SUSPENDED,
            updates=(
                FieldUpdate("trust_status", AffiliationTrustStatus.SUSPENDED.value),
                FieldUpdate("decision_reason_code", reason),
            ),
            current_version=current_version,
        )
    if (
        state is AffiliationTrustStatus.SUSPENDED
        and command is AffiliationTransitionCommand.RESTORE
    ):
        return _plan(
            old_state=state,
            new_state=AffiliationTrustStatus.ACTIVE,
            command=command,
            event_type=ProviderTrustAuditEvent.AFFILIATION_RESTORED,
            updates=(FieldUpdate("trust_status", AffiliationTrustStatus.ACTIVE.value),),
            clears=frozenset({"decision_reason_code"}),
            current_version=current_version,
        )
    if state in {
        AffiliationTrustStatus.ACTIVE,
        AffiliationTrustStatus.SUSPENDED,
    } and command in {
        AffiliationTransitionCommand.REVOKE,
        AffiliationTransitionCommand.EXPIRE,
        AffiliationTransitionCommand.LEAVE,
    }:
        reason = _require_text(facts.decision_reason_code, "decision_reason")
        if command is AffiliationTransitionCommand.REVOKE:
            new_state, event = (
                AffiliationTrustStatus.REVOKED,
                ProviderTrustAuditEvent.AFFILIATION_REVOKED,
            )
        elif command is AffiliationTransitionCommand.EXPIRE:
            new_state, event = (
                AffiliationTrustStatus.EXPIRED,
                ProviderTrustAuditEvent.AFFILIATION_EXPIRED,
            )
        else:
            new_state, event = (
                AffiliationTrustStatus.LEFT,
                ProviderTrustAuditEvent.AFFILIATION_LEFT,
            )
        return _plan(
            old_state=state,
            new_state=new_state,
            command=command,
            event_type=event,
            updates=(
                FieldUpdate("trust_status", new_state.value),
                FieldUpdate("decision_reason_code", reason),
            ),
            terminal=True,
            current_version=current_version,
        )
    raise LifecyclePolicyError("LIFECYCLE_TRANSITION_NOT_ALLOWED")
