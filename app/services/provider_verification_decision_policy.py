"""Pure registry observation decision policy and automation eligibility layer (Phase 5D).

This module implements the pure, fail-closed decision-policy layer that evaluates a
validated RegistryObservation against trusted current verification context. It produces
an immutable VerificationDecisionPlan containing a candidate decision and automation
eligibility.

Permanent authority invariant:
    REGISTRY OBSERVATION
    != DECISION POLICY
    != AUTOMATION ELIGIBILITY
    != SYSTEM EXECUTION AUTHORITY
    != LIFECYCLE MUTATION
    != CLINICAL AUTHORITY

Phase 5D defines AUTOMATION ELIGIBILITY, NOT automation authorization. It performs:
- Zero database reads or writes
- Zero transaction management
- Zero system principal or credential creation
- Zero lifecycle state mutations
- Zero clinical capability changes
- Zero external network requests
- Zero HTTP route additions
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import ClassVar

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
from app.services.provider_verification_registry import (
    FacilityLookupRequest,
    ProfessionalLookupRequest,
    RegistryObservation,
    RegistryResourceType,
)

PROVIDER_VERIFICATION_DECISION_POLICY_VERSION = (
    "provider-verification-decision-policy/1.0"
)
_MAX_GRACE_PERIOD_HOURS = 24

SYSTEM_ACTOR_PROVENANCE_GAP = (
    "Existing lifecycle verification and recheck planners require reviewer provenance "
    "through reviewer_id (persisted as String(128) and populated by the application layer "
    "from authenticated human provider actor identity). Existing lifecycle semantics do "
    "not yet distinguish a verified human reviewer actor from an authorized machine/system "
    "automation actor. Phase 5D must not fabricate a dummy string ('system', 'registry_worker') "
    "or fake provider identity to satisfy this field; Phase 5E execution authority must resolve this."
)

OBSERVATION_REQUEST_BINDING_GAP = (
    "RegistryObservation contains source, adapter, resource_type, lookup_purpose, outcome, "
    "and provenance metadata, but does not contain the queried registration authority code or "
    "number. The decision policy verifies that the supplied lookup request parameters match "
    "current lifecycle context, but cannot independently verify that the RegistryObservation "
    "was produced by that exact request. Phase 5E execution boundaries must not expose APIs "
    "that accept arbitrary independent request and observation pairings, but must establish "
    "structural invocation lineage (e.g. server-created validated lookup envelopes)."
)


# ---------------------------------------------------------------------------
# Decision Disposition & Reason Vocabularies
# ---------------------------------------------------------------------------


class VerificationDecisionDisposition(str, enum.Enum):
    """Closed vocabulary of decision dispositions returned by the pure policy."""

    NO_MUTATION_REQUIRED = "NO_MUTATION_REQUIRED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    SYSTEM_TRANSITION_CANDIDATE = "SYSTEM_TRANSITION_CANDIDATE"
    SYSTEM_FAIL_CLOSED_AND_REVIEW = "SYSTEM_FAIL_CLOSED_AND_REVIEW"
    LIFECYCLE_SEMANTIC_GAP = "LIFECYCLE_SEMANTIC_GAP"


class VerificationDecisionReason(str, enum.Enum):
    """Closed server-owned reason codes explaining the policy determination."""

    INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED = (
        "INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED"
    )
    POSITIVE_RECHECK_AUTOMATION_ELIGIBLE = "POSITIVE_RECHECK_AUTOMATION_ELIGIBLE"
    SOURCE_UNAVAILABLE_BOUNDED_GRACE = "SOURCE_UNAVAILABLE_BOUNDED_GRACE"
    SOURCE_UNAVAILABLE_NO_GRACE_FAIL_CLOSED = "SOURCE_UNAVAILABLE_NO_GRACE_FAIL_CLOSED"
    SOURCE_FAILURE_FAIL_CLOSED_REVIEW = "SOURCE_FAILURE_FAIL_CLOSED_REVIEW"
    SOURCE_CONTINUITY_REQUIRED = "SOURCE_CONTINUITY_REQUIRED"
    REGISTRATION_IDENTITY_MISMATCH = "REGISTRATION_IDENTITY_MISMATCH"
    RESOURCE_TYPE_MISMATCH = "RESOURCE_TYPE_MISMATCH"
    RECHECK_STATE_PRECONDITION_REQUIRED = "RECHECK_STATE_PRECONDITION_REQUIRED"
    RECHECK_GRACE_CANCELLATION_NOT_EXPRESSIBLE = (
        "RECHECK_GRACE_CANCELLATION_NOT_EXPRESSIBLE"
    )
    RECHECK_GRACE_CANCELLED = "RECHECK_GRACE_CANCELLED"
    OPEN_HUMAN_REVIEW_BLOCKS_AUTOMATION = "OPEN_HUMAN_REVIEW_BLOCKS_AUTOMATION"
    REPEATED_OUTAGE_EXISTING_GRACE_PRESERVED = (
        "REPEATED_OUTAGE_EXISTING_GRACE_PRESERVED"
    )
    TERMINAL_STATE_NO_AUTOMATION = "TERMINAL_STATE_NO_AUTOMATION"
    SUSPENDED_STATE_NO_AUTOMATION = "SUSPENDED_STATE_NO_AUTOMATION"
    ACTIVE_VERIFICATION_OBSERVATION_MATCH = "ACTIVE_VERIFICATION_OBSERVATION_MATCH"
    NEGATIVE_AMBIGUOUS_REVIEW_REQUIRED = "NEGATIVE_AMBIGUOUS_REVIEW_REQUIRED"
    ADVERSE_SIGNAL_PRESENT = "ADVERSE_SIGNAL_PRESENT"
    REGISTRATION_EXPIRED = "REGISTRATION_EXPIRED"
    IDENTITY_BINDING_NOT_MATCHED = "IDENTITY_BINDING_NOT_MATCHED"
    SYSTEM_ACTOR_PROVENANCE_GAP = "SYSTEM_ACTOR_PROVENANCE_GAP"
    OBSERVATION_REQUEST_BINDING_GAP = "OBSERVATION_REQUEST_BINDING_GAP"
    MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED = "MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED"


# ---------------------------------------------------------------------------
# Sanitized Policy Errors
# ---------------------------------------------------------------------------


class VerificationPolicyError(Exception):
    """Base exception for deterministic verification decision policy errors."""

    error_code: ClassVar[str] = "VERIFICATION_POLICY_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class VerificationPolicyInputError(VerificationPolicyError):
    """Raised when policy evaluation input fails validation without leaking sensitive data."""

    error_code: ClassVar[str] = "VERIFICATION_POLICY_INPUT_INVALID"


# ---------------------------------------------------------------------------
# Current-State Context Models (Supplied by Caller, Never Read from DB)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfessionalVerificationContext:
    """Immutable trusted current verification context for professional trust.

    Must never be populated from unvalidated client input. server_provenance_established
    and established_server_source_id must be derived from trusted evidence history.
    """

    current_status: ProfessionalVerificationStatus
    current_version: int
    registration_authority_code: str
    registration_number_normalized: str
    registration_valid_until: datetime | None = None
    previous_verification_valid: bool | None = None
    current_grace_expires_at: datetime | None = None
    current_recheck_failure_reason: VerificationSourceFailureReason | None = None
    authoritative_adverse_signal_at: datetime | None = None
    server_provenance_established: bool = False
    established_server_source_id: str | None = None
    open_human_review_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.current_status, ProfessionalVerificationStatus):
            raise VerificationPolicyInputError(
                "current_status must be a ProfessionalVerificationStatus"
            )
        if self.current_recheck_failure_reason is not None:
            if not isinstance(
                self.current_recheck_failure_reason,
                VerificationSourceFailureReason,
            ):
                raise VerificationPolicyInputError(
                    "current_recheck_failure_reason must be a VerificationSourceFailureReason"
                )
        if not isinstance(self.current_version, int) or self.current_version < 1:
            raise VerificationPolicyInputError(
                "current_version must be an integer >= 1"
            )
        if (
            not isinstance(self.registration_authority_code, str)
            or not self.registration_authority_code.strip()
        ):
            raise VerificationPolicyInputError(
                "registration_authority_code must be a non-empty string"
            )
        if (
            not isinstance(self.registration_number_normalized, str)
            or not self.registration_number_normalized.strip()
        ):
            raise VerificationPolicyInputError(
                "registration_number_normalized must be a non-empty string"
            )

        if self.registration_valid_until is not None:
            if (
                not isinstance(self.registration_valid_until, datetime)
                or self.registration_valid_until.tzinfo is None
            ):
                raise VerificationPolicyInputError(
                    "registration_valid_until must be timezone-aware"
                )
            object.__setattr__(
                self,
                "registration_valid_until",
                self.registration_valid_until.astimezone(timezone.utc),
            )

        if self.current_grace_expires_at is not None:
            if (
                not isinstance(self.current_grace_expires_at, datetime)
                or self.current_grace_expires_at.tzinfo is None
            ):
                raise VerificationPolicyInputError(
                    "current_grace_expires_at must be timezone-aware"
                )
            object.__setattr__(
                self,
                "current_grace_expires_at",
                self.current_grace_expires_at.astimezone(timezone.utc),
            )

        if self.authoritative_adverse_signal_at is not None:
            if (
                not isinstance(self.authoritative_adverse_signal_at, datetime)
                or self.authoritative_adverse_signal_at.tzinfo is None
            ):
                raise VerificationPolicyInputError(
                    "authoritative_adverse_signal_at must be timezone-aware"
                )
            object.__setattr__(
                self,
                "authoritative_adverse_signal_at",
                self.authoritative_adverse_signal_at.astimezone(timezone.utc),
            )


@dataclass(frozen=True, slots=True)
class FacilityVerificationContext:
    """Immutable trusted current verification context for facility trust.

    Facility verification has grace permanently disabled.
    """

    current_status: FacilityVerificationStatus
    current_version: int
    registration_authority_code: str
    registration_number_normalized: str
    server_provenance_established: bool = False
    established_server_source_id: str | None = None
    current_grace_expires_at: datetime | None = None
    current_recheck_failure_reason: VerificationSourceFailureReason | None = None
    open_human_review_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.current_status, FacilityVerificationStatus):
            raise VerificationPolicyInputError(
                "current_status must be a FacilityVerificationStatus"
            )
        if self.current_recheck_failure_reason is not None:
            if not isinstance(
                self.current_recheck_failure_reason,
                VerificationSourceFailureReason,
            ):
                raise VerificationPolicyInputError(
                    "current_recheck_failure_reason must be a VerificationSourceFailureReason"
                )
        if not isinstance(self.current_version, int) or self.current_version < 1:
            raise VerificationPolicyInputError(
                "current_version must be an integer >= 1"
            )
        if (
            not isinstance(self.registration_authority_code, str)
            or not self.registration_authority_code.strip()
        ):
            raise VerificationPolicyInputError(
                "registration_authority_code must be a non-empty string"
            )
        if (
            not isinstance(self.registration_number_normalized, str)
            or not self.registration_number_normalized.strip()
        ):
            raise VerificationPolicyInputError(
                "registration_number_normalized must be a non-empty string"
            )

        if self.current_grace_expires_at is not None:
            if (
                not isinstance(self.current_grace_expires_at, datetime)
                or self.current_grace_expires_at.tzinfo is None
            ):
                raise VerificationPolicyInputError(
                    "current_grace_expires_at must be timezone-aware"
                )
            object.__setattr__(
                self,
                "current_grace_expires_at",
                self.current_grace_expires_at.astimezone(timezone.utc),
            )


# ---------------------------------------------------------------------------
# Decision Plan (Pure Output, Zero Authority)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerificationDecisionPlan:
    """Immutable determination produced by evaluating an observation against context.

    Contains candidate proposals only. Never confers clinical permission or executes CAS.
    """

    resource_type: RegistryResourceType
    disposition: VerificationDecisionDisposition
    candidate_command: ProfessionalTransitionCommand | FacilityTransitionCommand | None
    expected_resource_version: int
    reason_code: VerificationDecisionReason
    requires_human_review: bool
    grace_expires_at: datetime | None
    source_id: str
    lookup_purpose: VerificationEvidenceLookupPurpose
    outcome: VerificationEvidenceOutcome


# ---------------------------------------------------------------------------
# Pure Decision Evaluation Functions
# ---------------------------------------------------------------------------


def evaluate_professional_observation(
    *,
    observation: RegistryObservation,
    request: ProfessionalLookupRequest,
    context: ProfessionalVerificationContext,
    now: datetime,
) -> VerificationDecisionPlan:
    """Evaluate a professional RegistryObservation against trusted lifecycle context."""
    if not isinstance(observation, RegistryObservation):
        raise VerificationPolicyInputError("observation must be a RegistryObservation")
    if observation.resource_type != RegistryResourceType.PROFESSIONAL:
        raise VerificationPolicyInputError(
            "observation must have resource_type PROFESSIONAL"
        )
    if not isinstance(request, ProfessionalLookupRequest):
        raise VerificationPolicyInputError(
            "request must be a ProfessionalLookupRequest"
        )
    if not isinstance(context, ProfessionalVerificationContext):
        raise VerificationPolicyInputError(
            "context must be a ProfessionalVerificationContext"
        )
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise VerificationPolicyInputError("now must be a timezone-aware datetime")
    if request.lookup_purpose != observation.lookup_purpose:
        raise VerificationPolicyInputError(
            "request and observation lookup purpose mismatch"
        )

    now_utc = now.astimezone(timezone.utc)

    # Cross-resource identity check: request must match current resource identity
    if (
        request.registration_authority_code != context.registration_authority_code
        or request.registration_number_normalized
        != context.registration_number_normalized
    ):
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.REGISTRATION_IDENTITY_MISMATCH,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # MANUAL_REVIEW: strictly human-adjudication path, never automate regardless of resource state or outcome
    if observation.lookup_purpose == VerificationEvidenceLookupPurpose.MANUAL_REVIEW:
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Terminal states: never automated, cannot resurrect authority
    terminal_statuses = (
        ProfessionalVerificationStatus.REJECTED,
        ProfessionalVerificationStatus.REVOKED,
        ProfessionalVerificationStatus.EXPIRED,
        ProfessionalVerificationStatus.VERIFICATION_STALE,
    )
    if context.current_status in terminal_statuses:
        if observation.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE:
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.PROFESSIONAL,
                disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                candidate_command=None,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION,
                requires_human_review=True,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.NO_MUTATION_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION,
            requires_human_review=False,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Suspended state: RESTORE is strictly human-gated in Phase 5D
    if context.current_status == ProfessionalVerificationStatus.SUSPENDED:
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.SUSPENDED_STATE_NO_AUTOMATION,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Initial verification: human-gated default
    initial_statuses = (
        ProfessionalVerificationStatus.PENDING_REVIEW,
        ProfessionalVerificationStatus.NOT_SUBMITTED,
    )
    if (
        context.current_status in initial_statuses
        or observation.lookup_purpose
        == VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION
    ):
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # State: VERIFIED
    if context.current_status == ProfessionalVerificationStatus.VERIFIED:
        if observation.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE:
            if (
                observation.lookup_purpose
                == VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK
            ):
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.NO_MUTATION_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.ACTIVE_VERIFICATION_OBSERVATION_MATCH,
                    requires_human_review=False,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            # RECHECK while still VERIFIED cannot execute COMPLETE_RECHECK directly
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.PROFESSIONAL,
                disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                candidate_command=None,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.RECHECK_STATE_PRECONDITION_REQUIRED,
                requires_human_review=True,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )

        if observation.outcome == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE:
            # Check all grace prerequisites (requires RECHECK purpose)
            has_prereqs = (
                observation.lookup_purpose == VerificationEvidenceLookupPurpose.RECHECK
                and context.previous_verification_valid is True
                and context.server_provenance_established is True
                and context.established_server_source_id == observation.source_id
                and context.authoritative_adverse_signal_at is None
                and context.registration_valid_until is not None
                and context.registration_valid_until > now_utc
            )
            if has_prereqs:
                max_allowed_grace = now_utc + timedelta(hours=_MAX_GRACE_PERIOD_HOURS)
                candidate_grace = min(
                    max_allowed_grace, context.registration_valid_until
                )  # type: ignore[type-var]
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE,
                    candidate_command=ProfessionalTransitionCommand.MARK_RECHECK_DUE,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.SOURCE_UNAVAILABLE_BOUNDED_GRACE,
                    requires_human_review=False,
                    grace_expires_at=candidate_grace,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            # Grace prerequisite failed: fail-closed without grace
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.PROFESSIONAL,
                disposition=VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW,
                candidate_command=ProfessionalTransitionCommand.MARK_RECHECK_DUE,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.SOURCE_UNAVAILABLE_NO_GRACE_FAIL_CLOSED,
                requires_human_review=True,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )

        # Non-outage failures/adverse results for VERIFIED: fail-closed without grace
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW,
            candidate_command=ProfessionalTransitionCommand.MARK_RECHECK_DUE,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.SOURCE_FAILURE_FAIL_CLOSED_REVIEW,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # State: RECHECK_DUE
    if context.current_status == ProfessionalVerificationStatus.RECHECK_DUE:
        # Positive recheck automation candidate
        if (
            observation.lookup_purpose == VerificationEvidenceLookupPurpose.RECHECK
            and observation.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE
        ):
            if (
                not context.server_provenance_established
                or context.established_server_source_id != observation.source_id
            ):
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.SOURCE_CONTINUITY_REQUIRED,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            if (
                observation.identity_binding_result
                != VerificationIdentityBindingResult.MATCHED
            ):
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.IDENTITY_BINDING_NOT_MATCHED,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            if context.authoritative_adverse_signal_at is not None:
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.ADVERSE_SIGNAL_PRESENT,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            if (
                context.registration_valid_until is not None
                and context.registration_valid_until <= now_utc
            ):
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.REGISTRATION_EXPIRED,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )

            if context.open_human_review_required:
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.OPEN_HUMAN_REVIEW_BLOCKS_AUTOMATION,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )

            # All positive recheck criteria satisfied
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.PROFESSIONAL,
                disposition=VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE,
                candidate_command=ProfessionalTransitionCommand.COMPLETE_RECHECK,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.POSITIVE_RECHECK_AUTOMATION_ELIGIBLE,
                requires_human_review=False,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )

        # Active grace handling: already RECHECK_DUE with active grace
        if (
            context.current_grace_expires_at is not None
            and context.current_grace_expires_at > now_utc
        ):
            if observation.outcome == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE:
                # Repeated outage: preserve existing grace, do not extend
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.PROFESSIONAL,
                    disposition=VerificationDecisionDisposition.NO_MUTATION_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.REPEATED_OUTAGE_EXISTING_GRACE_PRESERVED,
                    requires_human_review=False,
                    grace_expires_at=context.current_grace_expires_at,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            # Later non-outage failure during active grace -> cancel grace and fail closed
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.PROFESSIONAL,
                disposition=VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW,
                candidate_command=ProfessionalTransitionCommand.CANCEL_RECHECK_GRACE,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.RECHECK_GRACE_CANCELLED,
                requires_human_review=True,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )

        # RECHECK_DUE without active grace and negative/failure observation
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.PROFESSIONAL,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.NEGATIVE_AMBIGUOUS_REVIEW_REQUIRED,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Fallback for unexpected lifecycle states
    return VerificationDecisionPlan(
        resource_type=RegistryResourceType.PROFESSIONAL,
        disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
        candidate_command=None,
        expected_resource_version=context.current_version,
        reason_code=VerificationDecisionReason.NEGATIVE_AMBIGUOUS_REVIEW_REQUIRED,
        requires_human_review=True,
        grace_expires_at=None,
        source_id=observation.source_id,
        lookup_purpose=observation.lookup_purpose,
        outcome=observation.outcome,
    )


def evaluate_facility_observation(
    *,
    observation: RegistryObservation,
    request: FacilityLookupRequest,
    context: FacilityVerificationContext,
    now: datetime,
) -> VerificationDecisionPlan:
    """Evaluate a facility RegistryObservation against trusted lifecycle context."""
    if not isinstance(observation, RegistryObservation):
        raise VerificationPolicyInputError("observation must be a RegistryObservation")
    if observation.resource_type != RegistryResourceType.FACILITY:
        raise VerificationPolicyInputError(
            "observation must have resource_type FACILITY"
        )
    if not isinstance(request, FacilityLookupRequest):
        raise VerificationPolicyInputError("request must be a FacilityLookupRequest")
    if not isinstance(context, FacilityVerificationContext):
        raise VerificationPolicyInputError(
            "context must be a FacilityVerificationContext"
        )
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise VerificationPolicyInputError("now must be a timezone-aware datetime")
    if request.lookup_purpose != observation.lookup_purpose:
        raise VerificationPolicyInputError(
            "request and observation lookup purpose mismatch"
        )

    # Cross-resource identity check
    if (
        request.registration_authority_code != context.registration_authority_code
        or request.registration_number_normalized
        != context.registration_number_normalized
    ):
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.FACILITY,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.REGISTRATION_IDENTITY_MISMATCH,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # MANUAL_REVIEW: strictly human-adjudication path, never automate regardless of resource state or outcome
    if observation.lookup_purpose == VerificationEvidenceLookupPurpose.MANUAL_REVIEW:
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.FACILITY,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.MANUAL_REVIEW_PURPOSE_HUMAN_REQUIRED,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Terminal states: never automated, cannot resurrect
    terminal_statuses = (
        FacilityVerificationStatus.REJECTED,
        FacilityVerificationStatus.CLOSED,
    )
    if context.current_status in terminal_statuses:
        if observation.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE:
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.FACILITY,
                disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                candidate_command=None,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION,
                requires_human_review=True,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.FACILITY,
            disposition=VerificationDecisionDisposition.NO_MUTATION_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.TERMINAL_STATE_NO_AUTOMATION,
            requires_human_review=False,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Suspended state: RESTORE requires human review
    if context.current_status == FacilityVerificationStatus.SUSPENDED:
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.FACILITY,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.SUSPENDED_STATE_NO_AUTOMATION,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Initial verification: human-gated default
    initial_statuses = (
        FacilityVerificationStatus.PENDING_VERIFICATION,
        FacilityVerificationStatus.DRAFT,
    )
    if (
        context.current_status in initial_statuses
        or observation.lookup_purpose
        == VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION
    ):
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.FACILITY,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.INITIAL_VERIFICATION_HUMAN_GATE_REQUIRED,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # State: VERIFIED
    if context.current_status == FacilityVerificationStatus.VERIFIED:
        if observation.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE:
            if (
                observation.lookup_purpose
                == VerificationEvidenceLookupPurpose.ADVERSE_SIGNAL_CHECK
            ):
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.FACILITY,
                    disposition=VerificationDecisionDisposition.NO_MUTATION_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.ACTIVE_VERIFICATION_OBSERVATION_MATCH,
                    requires_human_review=False,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.FACILITY,
                disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                candidate_command=None,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.RECHECK_STATE_PRECONDITION_REQUIRED,
                requires_human_review=True,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )

        # Any revalidation requirement for facility: MARK_RECHECK_REQUIRED without grace (grace is disabled for facilities)
        if observation.outcome == VerificationEvidenceOutcome.SOURCE_UNAVAILABLE:
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.FACILITY,
                disposition=VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW,
                candidate_command=FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.SOURCE_UNAVAILABLE_NO_GRACE_FAIL_CLOSED,
                requires_human_review=True,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )

        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.FACILITY,
            disposition=VerificationDecisionDisposition.SYSTEM_FAIL_CLOSED_AND_REVIEW,
            candidate_command=FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.SOURCE_FAILURE_FAIL_CLOSED_REVIEW,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # State: RECHECK_REQUIRED
    if context.current_status == FacilityVerificationStatus.RECHECK_REQUIRED:
        if (
            observation.lookup_purpose == VerificationEvidenceLookupPurpose.RECHECK
            and observation.outcome == VerificationEvidenceOutcome.CONFIRMED_ACTIVE
        ):
            if (
                not context.server_provenance_established
                or context.established_server_source_id != observation.source_id
            ):
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.FACILITY,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.SOURCE_CONTINUITY_REQUIRED,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )
            if (
                observation.identity_binding_result
                != VerificationIdentityBindingResult.MATCHED
            ):
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.FACILITY,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.IDENTITY_BINDING_NOT_MATCHED,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )

            if context.open_human_review_required:
                return VerificationDecisionPlan(
                    resource_type=RegistryResourceType.FACILITY,
                    disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
                    candidate_command=None,
                    expected_resource_version=context.current_version,
                    reason_code=VerificationDecisionReason.OPEN_HUMAN_REVIEW_BLOCKS_AUTOMATION,
                    requires_human_review=True,
                    grace_expires_at=None,
                    source_id=observation.source_id,
                    lookup_purpose=observation.lookup_purpose,
                    outcome=observation.outcome,
                )

            # Positive facility recheck automation candidate
            return VerificationDecisionPlan(
                resource_type=RegistryResourceType.FACILITY,
                disposition=VerificationDecisionDisposition.SYSTEM_TRANSITION_CANDIDATE,
                candidate_command=FacilityTransitionCommand.COMPLETE_RECHECK,
                expected_resource_version=context.current_version,
                reason_code=VerificationDecisionReason.POSITIVE_RECHECK_AUTOMATION_ELIGIBLE,
                requires_human_review=False,
                grace_expires_at=None,
                source_id=observation.source_id,
                lookup_purpose=observation.lookup_purpose,
                outcome=observation.outcome,
            )

        # Negative / ambiguous / auth / integrity while already RECHECK_REQUIRED: human review required, no auto CLOSE/REJECT
        return VerificationDecisionPlan(
            resource_type=RegistryResourceType.FACILITY,
            disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
            candidate_command=None,
            expected_resource_version=context.current_version,
            reason_code=VerificationDecisionReason.NEGATIVE_AMBIGUOUS_REVIEW_REQUIRED,
            requires_human_review=True,
            grace_expires_at=None,
            source_id=observation.source_id,
            lookup_purpose=observation.lookup_purpose,
            outcome=observation.outcome,
        )

    # Fallback
    return VerificationDecisionPlan(
        resource_type=RegistryResourceType.FACILITY,
        disposition=VerificationDecisionDisposition.HUMAN_REVIEW_REQUIRED,
        candidate_command=None,
        expected_resource_version=context.current_version,
        reason_code=VerificationDecisionReason.NEGATIVE_AMBIGUOUS_REVIEW_REQUIRED,
        requires_human_review=True,
        grace_expires_at=None,
        source_id=observation.source_id,
        lookup_purpose=observation.lookup_purpose,
        outcome=observation.outcome,
    )
