"""Durable lifecycle for verified patient registration-recovery manual review.

The service stores only authority metadata. Patient OTP proof opens a case; a
separate, live provider-reviewer authority may claim and resolve it. Resolution
revalidates the exact registration graph under the same PostgreSQL advisory-lock
domain used by automatic account recovery before any bounded mutation is staged.
No operation in this module issues patient sessions, device authority, or consent.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.models.patient_registration_recovery_review import (
    REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
    REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
    PatientRegistrationRecoveryReviewCase,
    PatientRegistrationRecoveryReviewDisposition,
    RegistrationRecoveryReviewOutcome,
    RegistrationRecoveryReviewReason,
    RegistrationRecoveryReviewStatus,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_registration_recovery_service import (
    REPAIR_REBIND_AND_RESTORE_RECORD,
    REPAIR_REBIND_MERGED_IDENTITY,
    REPAIR_RESTORE_RECORD,
    SUPABASE_PROVIDER,
    RegistrationRecoveryInspection,
    _lock_key as registration_recovery_lock_key,
    inspect_patient_registration_recovery,
)

REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT = "REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT"
REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN = (
    "REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN"
)
REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND = "REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND"
REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT = "REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT"
REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED = "REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED"
REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH = "REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH"
REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED = "REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED"
REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED = "REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED"
REGISTRATION_RECOVERY_REVIEW_IDEMPOTENCY_CONFLICT = (
    "REGISTRATION_RECOVERY_REVIEW_IDEMPOTENCY_CONFLICT"
)
REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID = "REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID"
REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED = (
    "REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED"
)

_REVIEW_OPENED_EVENT = "PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED"
_REVIEW_CLAIMED_EVENT = "PATIENT_REGISTRATION_RECOVERY_REVIEW_CLAIMED"
_REVIEW_RESOLVED_EVENT = "PATIENT_REGISTRATION_RECOVERY_REVIEW_RESOLVED"
_REVIEW_REJECTED_EVENT = "PATIENT_REGISTRATION_RECOVERY_REVIEW_REJECTED"
_REVIEW_ESCALATED_EVENT = "PATIENT_REGISTRATION_RECOVERY_REVIEW_SECURITY_ESCALATED"

_TERMINAL_STATES = frozenset(
    {
        RegistrationRecoveryReviewStatus.RESOLVED.value,
        RegistrationRecoveryReviewStatus.REJECTED.value,
        RegistrationRecoveryReviewStatus.SECURITY_ESCALATED.value,
    }
)
_CASE_REFERENCE_PREFIX = "RRC-"
_CASE_REFERENCE_LENGTH = 28

_REASON_MAP: dict[str, RegistrationRecoveryReviewReason] = {
    "ERASURE_STATE_PRESENT": RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT,
    "CANONICAL_ERASURE_STATE_PRESENT": RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT,
    "IDENTITY_REVOKED": RegistrationRecoveryReviewReason.IDENTITY_REVOKED,
    "MULTIPLE_SOURCE_IDENTITIES": RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
    "CANONICAL_IDENTITY_CONFLICT": RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
    "DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE": RegistrationRecoveryReviewReason.PATIENT_DELETED_WITHOUT_MERGE,
    "MERGE_TOMBSTONE_CYCLE": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "MERGE_TOMBSTONE_CHAIN_TOO_DEEP": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "CANONICAL_PATIENT_UNAVAILABLE": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "LINKED_PATIENT_MISSING": RegistrationRecoveryReviewReason.SECURITY_CONCERN,
}

_REPAIR_REASON_BY_OUTCOME = {
    RegistrationRecoveryReviewOutcome.RESTORE_MISSING_RECORD_ANCHOR: (
        RegistrationRecoveryReviewReason.MISSING_RECORD_ANCHOR
    ),
    RegistrationRecoveryReviewOutcome.REBIND_MERGED_IDENTITY: (
        RegistrationRecoveryReviewReason.MERGED_IDENTITY_REBIND_REQUIRED
    ),
}


class RegistrationRecoveryReviewerAuthority(Protocol):
    reviewer_id: str
    authority_version: str
    session_binding: str


class PatientRegistrationRecoveryReviewError(RuntimeError):
    """Stable, value-free durable review failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_registration_recovery_review_reason(
    source_reason: str | None,
) -> RegistrationRecoveryReviewReason:
    """Map internal classifier detail to the closed durable review vocabulary."""

    if not source_reason:
        return RegistrationRecoveryReviewReason.SECURITY_CONCERN
    return _REASON_MAP.get(
        source_reason, RegistrationRecoveryReviewReason.SECURITY_CONCERN
    )


def provider_subject_hash(provider_subject: str) -> str:
    """Return a domain-separated one-way identifier for the verified subject."""

    return hashlib.sha256(
        (
            "registration-recovery-review:provider-subject:"
            f"{SUPABASE_PROVIDER}:{provider_subject}"
        ).encode("utf-8")
    ).hexdigest()


def _operation_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _creation_idempotency_key(attempt_id: str) -> str:
    digest = hashlib.sha256(
        f"registration-recovery-review:create:{attempt_id}".encode("utf-8")
    ).hexdigest()
    return f"registration-recovery-review:create:{digest}"


def _case_reference() -> str:
    return f"{_CASE_REFERENCE_PREFIX}{secrets.token_hex(12).upper()}"


def _valid_case_reference(value: str) -> bool:
    if len(value) != _CASE_REFERENCE_LENGTH or not value.startswith(_CASE_REFERENCE_PREFIX):
        return False
    suffix = value[len(_CASE_REFERENCE_PREFIX) :]
    return len(suffix) == 24 and suffix.upper() == suffix and all(
        char in "0123456789ABCDEF" for char in suffix
    )


def _valid_graph_fingerprint(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _audit_context() -> AuditContext:
    return AuditContext.platform(domain=AuditDomain.AUTH)


def _assert_existing_matches(
    case: PatientRegistrationRecoveryReviewCase,
    *,
    identity_id: uuid.UUID,
    patient_id: uuid.UUID | None,
    subject_hash: str,
    graph_fingerprint: str,
    reason_code: str,
    operation_hash: str,
) -> None:
    if (
        case.provider != SUPABASE_PROVIDER
        or case.provider_subject_hash != subject_hash
        or case.identity_id != identity_id
        or case.patient_id != patient_id
        or case.graph_fingerprint != graph_fingerprint
        or list(case.reason_codes or []) != [reason_code]
        or case.creation_operation_hash != operation_hash
        or case.contract_version != REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION
        or case.policy_version != REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
        )


async def _load_case(
    db: AsyncSession,
    case_reference: str,
    *,
    lock: bool,
) -> PatientRegistrationRecoveryReviewCase:
    if not _valid_case_reference(case_reference):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND
        )
    query = select(PatientRegistrationRecoveryReviewCase).where(
        PatientRegistrationRecoveryReviewCase.case_reference == case_reference
    )
    if lock:
        query = query.with_for_update()
    case = (await db.execute(query)).scalar_one_or_none()
    if case is None:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND
        )
    return case


def _assert_reviewer_assignment(
    case: PatientRegistrationRecoveryReviewCase,
    reviewer: RegistrationRecoveryReviewerAuthority,
) -> None:
    if case.assigned_reviewer_id != reviewer.reviewer_id:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED
        )
    if case.review_session_binding != reviewer.session_binding:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH
        )


def _normalize_reason_codes(
    reason_codes: Iterable[RegistrationRecoveryReviewReason],
) -> tuple[RegistrationRecoveryReviewReason, ...]:
    normalized = tuple(reason_codes)
    if not normalized or len(normalized) != len(set(normalized)):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID
        )
    return normalized


def _validate_resolution_policy(
    case: PatientRegistrationRecoveryReviewCase,
    *,
    outcome: RegistrationRecoveryReviewOutcome,
    reason_codes: tuple[RegistrationRecoveryReviewReason, ...],
) -> None:
    requested = set(reason_codes)
    existing = {RegistrationRecoveryReviewReason(code) for code in case.reason_codes}
    if outcome in _REPAIR_REASON_BY_OUTCOME:
        required = _REPAIR_REASON_BY_OUTCOME[outcome]
        if requested != {required} or required not in existing:
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED
            )
        return
    allowed = existing | {RegistrationRecoveryReviewReason.SECURITY_CONCERN}
    if not requested.issubset(allowed):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID
        )
    if (
        outcome is RegistrationRecoveryReviewOutcome.SECURITY_ESCALATION_REQUIRED
        and RegistrationRecoveryReviewReason.SECURITY_CONCERN not in requested
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID
        )


async def _load_and_lock_identity_graph(
    db: AsyncSession,
    *,
    case: PatientRegistrationRecoveryReviewCase,
) -> tuple[PatientAuthIdentity, RegistrationRecoveryInspection]:
    subject_row = (
        await db.execute(
            select(PatientAuthIdentity.provider, PatientAuthIdentity.provider_subject).where(
                PatientAuthIdentity.identity_id == case.identity_id
            )
        )
    ).one_or_none()
    if subject_row is None or subject_row.provider != SUPABASE_PROVIDER:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED
        )
    provider_subject = str(subject_row.provider_subject)
    if not secrets.compare_digest(
        provider_subject_hash(provider_subject), case.provider_subject_hash
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED
        )

    bind = db.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", None) == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": registration_recovery_lock_key(provider_subject)},
        )

    identity = (
        await db.execute(
            select(PatientAuthIdentity)
            .where(PatientAuthIdentity.identity_id == case.identity_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        identity is None
        or identity.provider != SUPABASE_PROVIDER
        or identity.provider_subject != provider_subject
        or not secrets.compare_digest(
            provider_subject_hash(identity.provider_subject), case.provider_subject_hash
        )
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED
        )

    inspection = await inspect_patient_registration_recovery(
        db, provider_subject=provider_subject
    )
    if inspection.graph_fingerprint != case.graph_fingerprint:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED
        )
    if case.patient_id is not None and inspection.patient_id != str(case.patient_id):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED
        )
    return identity, inspection


async def open_registration_recovery_review_case(
    db: AsyncSession,
    *,
    inspection: RegistrationRecoveryInspection,
    attempt_id: str,
) -> PatientRegistrationRecoveryReviewCase:
    if (
        inspection.disposition != "manual_review"
        or not inspection.provider_subject
        or not attempt_id
        or not _valid_graph_fingerprint(inspection.graph_fingerprint)
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN
        )

    try:
        inspected_patient_id = uuid.UUID(str(inspection.patient_id))
    except (TypeError, ValueError) as exc:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN
        ) from exc

    identity = (
        await db.execute(
            select(PatientAuthIdentity)
            .where(
                PatientAuthIdentity.provider == SUPABASE_PROVIDER,
                PatientAuthIdentity.provider_subject == inspection.provider_subject,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        identity is None
        or identity.provider != SUPABASE_PROVIDER
        or identity.provider_subject != inspection.provider_subject
        or identity.patient_id != inspected_patient_id
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
        )

    current_patient = await db.get(Patient, inspected_patient_id)
    stored_patient_id = inspected_patient_id if current_patient is not None else None
    normalized_reason = normalize_registration_recovery_review_reason(
        inspection.reason_code
    ).value
    subject_hash = provider_subject_hash(inspection.provider_subject)
    operation_payload = {
        "contract_version": REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
        "graph_fingerprint": inspection.graph_fingerprint,
        "identity_id": str(identity.identity_id),
        "patient_id": str(stored_patient_id) if stored_patient_id else None,
        "policy_version": REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
        "provider": SUPABASE_PROVIDER,
        "provider_subject_hash": subject_hash,
        "reason_codes": [normalized_reason],
    }
    operation_hash = _operation_hash(operation_payload)
    idempotency_key = _creation_idempotency_key(attempt_id)

    keyed = (
        await db.execute(
            select(PatientRegistrationRecoveryReviewCase).where(
                PatientRegistrationRecoveryReviewCase.creation_idempotency_key
                == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if keyed is not None:
        _assert_existing_matches(
            keyed,
            identity_id=identity.identity_id,
            patient_id=stored_patient_id,
            subject_hash=subject_hash,
            graph_fingerprint=inspection.graph_fingerprint,
            reason_code=normalized_reason,
            operation_hash=operation_hash,
        )
        case = keyed
    else:
        existing = (
            await db.execute(
                select(PatientRegistrationRecoveryReviewCase).where(
                    PatientRegistrationRecoveryReviewCase.provider == SUPABASE_PROVIDER,
                    PatientRegistrationRecoveryReviewCase.provider_subject_hash == subject_hash,
                    PatientRegistrationRecoveryReviewCase.graph_fingerprint
                    == inspection.graph_fingerprint,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            _assert_existing_matches(
                existing,
                identity_id=identity.identity_id,
                patient_id=stored_patient_id,
                subject_hash=subject_hash,
                graph_fingerprint=inspection.graph_fingerprint,
                reason_code=normalized_reason,
                operation_hash=operation_hash,
            )
            case = existing
        else:
            now = datetime.now(timezone.utc)
            case = PatientRegistrationRecoveryReviewCase(
                case_reference=_case_reference(),
                provider=SUPABASE_PROVIDER,
                provider_subject_hash=subject_hash,
                identity_id=identity.identity_id,
                patient_id=stored_patient_id,
                graph_fingerprint=inspection.graph_fingerprint,
                reason_codes=[normalized_reason],
                status=RegistrationRecoveryReviewStatus.PENDING.value,
                version=1,
                assigned_reviewer_id=None,
                assigned_reviewer_role=None,
                reviewer_authority_version=None,
                review_session_binding=None,
                creation_idempotency_key=idempotency_key,
                creation_operation_hash=operation_hash,
                contract_version=REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
                policy_version=REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
                created_at=now,
                claimed_at=None,
                resolved_at=None,
            )
            db.add(case)
            await db.flush()

    await enqueue_audit_event(
        db,
        audit_context=_audit_context(),
        idempotency_key=f"registration-recovery-review:{case.id}:opened",
        actor_id="PATIENT_REGISTRATION_RECOVERY",
        event_type=_REVIEW_OPENED_EVENT,
        target_id=case.case_reference,
        patient_id=str(case.patient_id) if case.patient_id is not None else None,
        status="PENDING",
        metadata={
            "case_reference": case.case_reference,
            "reason_codes": list(case.reason_codes),
            "status": case.status,
            "contract_version": case.contract_version,
            "policy_version": case.policy_version,
        },
    )
    return case


async def patient_review_status(
    db: AsyncSession, *, case_reference: str
) -> dict[str, Any]:
    case = await _load_case(db, case_reference, lock=False)
    terminal = case.status in _TERMINAL_STATES
    if case.status == RegistrationRecoveryReviewStatus.RESOLVED.value:
        next_action = "RESTART_ACCOUNT_RECOVERY"
    elif terminal:
        next_action = "CONTACT_SUPPORT"
    else:
        next_action = "WAIT_FOR_REVIEW"
    return {
        "case_reference": case.case_reference,
        "status": case.status,
        "terminal": terminal,
        "next_action": next_action,
        "created_at": case.created_at,
        "resolved_at": case.resolved_at,
    }


async def list_reviewer_cases(
    db: AsyncSession,
    *,
    reviewer: RegistrationRecoveryReviewerAuthority,
    status_filter: RegistrationRecoveryReviewStatus | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = select(PatientRegistrationRecoveryReviewCase).where(
        or_(
            PatientRegistrationRecoveryReviewCase.status
            == RegistrationRecoveryReviewStatus.PENDING.value,
            PatientRegistrationRecoveryReviewCase.assigned_reviewer_id
            == reviewer.reviewer_id,
        )
    )
    if status_filter is not None:
        query = query.where(
            PatientRegistrationRecoveryReviewCase.status == status_filter.value
        )
    query = query.order_by(PatientRegistrationRecoveryReviewCase.created_at).limit(limit)
    cases = list((await db.scalars(query)).all())
    return [reviewer_case_metadata(case, reviewer=reviewer) for case in cases]


def reviewer_case_metadata(
    case: PatientRegistrationRecoveryReviewCase,
    *,
    reviewer: RegistrationRecoveryReviewerAuthority,
) -> dict[str, Any]:
    return {
        "case_reference": case.case_reference,
        "patient_id": str(case.patient_id) if case.patient_id is not None else None,
        "status": case.status,
        "reason_codes": list(case.reason_codes),
        "version": case.version,
        "assigned_to_current_reviewer": case.assigned_reviewer_id
        == reviewer.reviewer_id,
        "created_at": case.created_at,
        "claimed_at": case.claimed_at,
        "resolved_at": case.resolved_at,
        "contract_version": case.contract_version,
        "policy_version": case.policy_version,
    }


async def read_reviewer_case(
    db: AsyncSession,
    *,
    case_reference: str,
    reviewer: RegistrationRecoveryReviewerAuthority,
) -> dict[str, Any]:
    case = await _load_case(db, case_reference, lock=False)
    if (
        case.status != RegistrationRecoveryReviewStatus.PENDING.value
        and case.assigned_reviewer_id != reviewer.reviewer_id
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED
        )
    return reviewer_case_metadata(case, reviewer=reviewer)


async def claim_reviewer_case(
    db: AsyncSession,
    *,
    case_reference: str,
    reviewer: RegistrationRecoveryReviewerAuthority,
    expected_version: int,
) -> PatientRegistrationRecoveryReviewCase:
    case = await _load_case(db, case_reference, lock=True)
    if case.status in _TERMINAL_STATES:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED
        )
    if case.status == RegistrationRecoveryReviewStatus.IN_REVIEW.value:
        if case.assigned_reviewer_id != reviewer.reviewer_id:
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
            )
        if case.review_session_binding != reviewer.session_binding:
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH
            )
        if expected_version not in {case.version, case.version - 1}:
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT
            )
        return case
    if case.status != RegistrationRecoveryReviewStatus.PENDING.value:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
        )
    if case.version != expected_version:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT
        )

    now = datetime.now(timezone.utc)
    prior_version = case.version
    case.status = RegistrationRecoveryReviewStatus.IN_REVIEW.value
    case.assigned_reviewer_id = reviewer.reviewer_id
    case.assigned_reviewer_role = "registration_recovery_reviewer"
    case.reviewer_authority_version = reviewer.authority_version
    case.review_session_binding = reviewer.session_binding
    case.claimed_at = now
    case.version += 1
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(),
        idempotency_key=f"registration-recovery-review:{case.id}:claimed:{case.version}",
        actor_id=reviewer.reviewer_id,
        event_type=_REVIEW_CLAIMED_EVENT,
        target_id=case.case_reference,
        patient_id=str(case.patient_id) if case.patient_id is not None else None,
        status=case.status,
        metadata={
            "prior_version": prior_version,
            "version": case.version,
            "authority_version": reviewer.authority_version,
        },
    )
    return case


async def recover_reviewer_session(
    db: AsyncSession,
    *,
    case_reference: str,
    reviewer: RegistrationRecoveryReviewerAuthority,
    expected_version: int,
) -> PatientRegistrationRecoveryReviewCase:
    case = await _load_case(db, case_reference, lock=True)
    if case.status in _TERMINAL_STATES:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED
        )
    if (
        case.status != RegistrationRecoveryReviewStatus.IN_REVIEW.value
        or case.assigned_reviewer_id != reviewer.reviewer_id
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED
        )
    if case.review_session_binding == reviewer.session_binding:
        if expected_version not in {case.version, case.version - 1}:
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT
            )
        return case
    if case.version != expected_version:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT
        )

    prior_version = case.version
    case.review_session_binding = reviewer.session_binding
    case.reviewer_authority_version = reviewer.authority_version
    case.version += 1
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(),
        idempotency_key=(
            f"registration-recovery-review:{case.id}:session-recovered:{case.version}"
        ),
        actor_id=reviewer.reviewer_id,
        event_type=_REVIEW_CLAIMED_EVENT,
        target_id=case.case_reference,
        patient_id=str(case.patient_id) if case.patient_id is not None else None,
        status=case.status,
        metadata={
            "action": "SESSION_RECOVERED",
            "prior_version": prior_version,
            "version": case.version,
            "authority_version": reviewer.authority_version,
        },
    )
    return case


async def resolve_reviewer_case(
    db: AsyncSession,
    *,
    case_reference: str,
    reviewer: RegistrationRecoveryReviewerAuthority,
    expected_version: int,
    idempotency_key: str,
    outcome: RegistrationRecoveryReviewOutcome,
    reason_codes: Iterable[RegistrationRecoveryReviewReason],
) -> tuple[PatientRegistrationRecoveryReviewCase, PatientRegistrationRecoveryReviewDisposition]:
    if not idempotency_key or len(idempotency_key) > 192:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID
        )
    normalized_reasons = _normalize_reason_codes(reason_codes)
    case = await _load_case(db, case_reference, lock=True)
    operation_hash = _operation_hash(
        {
            "case_id": str(case.id),
            "expected_version": expected_version,
            "idempotency_key": idempotency_key,
            "outcome": outcome.value,
            "reason_codes": sorted(reason.value for reason in normalized_reasons),
            "reviewer_id": reviewer.reviewer_id,
        }
    )
    existing_by_key = (
        await db.execute(
            select(PatientRegistrationRecoveryReviewDisposition).where(
                PatientRegistrationRecoveryReviewDisposition.idempotency_key
                == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if (
            existing_by_key.case_id != case.id
            or existing_by_key.reviewer_id != reviewer.reviewer_id
            or existing_by_key.operation_hash != operation_hash
        ):
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_IDEMPOTENCY_CONFLICT
            )
        return case, existing_by_key

    if case.status in _TERMINAL_STATES:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED
        )
    if case.status != RegistrationRecoveryReviewStatus.IN_REVIEW.value:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
        )
    _assert_reviewer_assignment(case, reviewer)
    if case.version != expected_version:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT
        )
    _validate_resolution_policy(
        case, outcome=outcome, reason_codes=normalized_reasons
    )

    identity, inspection = await _load_and_lock_identity_graph(db, case=case)
    target_patient_id = uuid.UUID(inspection.target_patient_id)
    if outcome is RegistrationRecoveryReviewOutcome.RESTORE_MISSING_RECORD_ANCHOR:
        if (
            not inspection.repairable
            or inspection.repair_kind != REPAIR_RESTORE_RECORD
        ):
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED
            )
        db.add(PatientRecord(patient_id=target_patient_id))
    elif outcome is RegistrationRecoveryReviewOutcome.REBIND_MERGED_IDENTITY:
        if (
            not inspection.repairable
            or inspection.repair_kind
            not in {REPAIR_REBIND_MERGED_IDENTITY, REPAIR_REBIND_AND_RESTORE_RECORD}
            or identity.revoked_at is not None
        ):
            raise PatientRegistrationRecoveryReviewError(
                REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED
            )
        identity.patient_id = target_patient_id
        if inspection.repair_kind == REPAIR_REBIND_AND_RESTORE_RECORD:
            db.add(PatientRecord(patient_id=target_patient_id))
    elif outcome not in {
        RegistrationRecoveryReviewOutcome.NO_REPAIR,
        RegistrationRecoveryReviewOutcome.SECURITY_ESCALATION_REQUIRED,
    }:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID
        )

    now = datetime.now(timezone.utc)
    prior_version = case.version
    if outcome is RegistrationRecoveryReviewOutcome.SECURITY_ESCALATION_REQUIRED:
        terminal_status = RegistrationRecoveryReviewStatus.SECURITY_ESCALATED
        event_type = _REVIEW_ESCALATED_EVENT
    elif outcome is RegistrationRecoveryReviewOutcome.NO_REPAIR:
        terminal_status = RegistrationRecoveryReviewStatus.REJECTED
        event_type = _REVIEW_REJECTED_EVENT
    else:
        terminal_status = RegistrationRecoveryReviewStatus.RESOLVED
        event_type = _REVIEW_RESOLVED_EVENT

    disposition = PatientRegistrationRecoveryReviewDisposition(
        case_id=case.id,
        reviewer_id=reviewer.reviewer_id,
        reviewer_role="registration_recovery_reviewer",
        reviewer_authority_version=reviewer.authority_version,
        outcome=outcome.value,
        reason_codes=[reason.value for reason in normalized_reasons],
        prior_case_version=prior_version,
        idempotency_key=idempotency_key,
        operation_hash=operation_hash,
        contract_version=REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
        policy_version=REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
        submitted_at=now,
    )
    db.add(disposition)
    case.status = terminal_status.value
    case.resolved_at = now
    case.version += 1
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(),
        idempotency_key=f"registration-recovery-review:{case.id}:terminal:{operation_hash}",
        actor_id=reviewer.reviewer_id,
        event_type=event_type,
        target_id=case.case_reference,
        patient_id=str(target_patient_id),
        status=case.status,
        metadata={
            "outcome": outcome.value,
            "reason_codes": [reason.value for reason in normalized_reasons],
            "prior_version": prior_version,
            "version": case.version,
            "contract_version": case.contract_version,
            "policy_version": case.policy_version,
        },
    )
    return case, disposition


__all__ = [
    "REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED",
    "REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED",
    "REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT",
    "REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN",
    "REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND",
    "REGISTRATION_RECOVERY_REVIEW_IDEMPOTENCY_CONFLICT",
    "REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID",
    "REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED",
    "REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH",
    "REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED",
    "REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT",
    "PatientRegistrationRecoveryReviewError",
    "claim_reviewer_case",
    "list_reviewer_cases",
    "normalize_registration_recovery_review_reason",
    "open_registration_recovery_review_case",
    "patient_review_status",
    "provider_subject_hash",
    "read_reviewer_case",
    "recover_reviewer_session",
    "resolve_reviewer_case",
    "reviewer_case_metadata",
]
