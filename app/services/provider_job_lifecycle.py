"""Value-free lifecycle service for durable asynchronous provider attempts.

Scenario 6 A2 deliberately stops at the provider-job boundary.  This module
owns state transitions, bounded reconciliation claims, and transactional audit
events; it never talks to a provider, reads document bytes, or creates clinical
projections.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import ExtractionJob, ExtractionProviderJobRecord
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event


class ProviderJobStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    IN_PROGRESS = "IN_PROGRESS"
    LOCAL_WAIT_EXPIRED = "LOCAL_WAIT_EXPIRED"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    FETCHING_RESULTS = "FETCHING_RESULTS"
    VALIDATING_COMPLETE_RESULT = "VALIDATING_COMPLETE_RESULT"
    COMPLETE = "COMPLETE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    PROVIDER_UNREACHABLE_MANUAL_REVIEW = "PROVIDER_UNREACHABLE_MANUAL_REVIEW"
    SUPERSEDED = "SUPERSEDED"


_S = ProviderJobStatus
LEGAL_PROVIDER_JOB_TRANSITIONS = MappingProxyType(
    {
        _S.CREATED.value: frozenset({_S.SUBMITTING.value, _S.FAILED_TERMINAL.value}),
        _S.SUBMITTING.value: frozenset(
            {
                _S.SUBMITTED.value,
                _S.LOCAL_WAIT_EXPIRED.value,
                _S.FAILED_RETRYABLE.value,
                _S.FAILED_TERMINAL.value,
            }
        ),
        _S.SUBMITTED.value: frozenset(
            {
                _S.IN_PROGRESS.value,
                _S.SUCCEEDED.value,
                _S.LOCAL_WAIT_EXPIRED.value,
                _S.FAILED_RETRYABLE.value,
                _S.FAILED_TERMINAL.value,
            }
        ),
        _S.IN_PROGRESS.value: frozenset(
            {
                _S.SUCCEEDED.value,
                _S.LOCAL_WAIT_EXPIRED.value,
                _S.FAILED_RETRYABLE.value,
                _S.FAILED_TERMINAL.value,
            }
        ),
        # Budget/deadline exhaustion is the one deliberate direct escalation
        # from a locally expired row; no provider result is inferred.
        _S.LOCAL_WAIT_EXPIRED.value: frozenset(
            {_S.RECONCILING.value, _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value}
        ),
        _S.RECONCILING.value: frozenset(
            {
                _S.SUBMITTED.value,
                _S.IN_PROGRESS.value,
                _S.SUCCEEDED.value,
                _S.FAILED_RETRYABLE.value,
                _S.FAILED_TERMINAL.value,
                _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value,
            }
        ),
        _S.SUCCEEDED.value: frozenset(
            {_S.FETCHING_RESULTS.value, _S.FAILED_TERMINAL.value}
        ),
        _S.FETCHING_RESULTS.value: frozenset(
            {
                _S.VALIDATING_COMPLETE_RESULT.value,
                _S.RECONCILING.value,
                _S.FAILED_TERMINAL.value,
                _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value,
            }
        ),
        _S.VALIDATING_COMPLETE_RESULT.value: frozenset(
            {_S.COMPLETE.value, _S.FAILED_TERMINAL.value}
        ),
        _S.COMPLETE.value: frozenset(),
        _S.FAILED_RETRYABLE.value: frozenset(),
        _S.FAILED_TERMINAL.value: frozenset(),
        _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value: frozenset(),
        _S.SUPERSEDED.value: frozenset(),
    }
)
# Stable public names for callers and contract tests.
PROVIDER_JOB_TRANSITIONS = LEGAL_PROVIDER_JOB_TRANSITIONS
LEGAL_TRANSITIONS = LEGAL_PROVIDER_JOB_TRANSITIONS
TERMINAL_PROVIDER_JOB_STATES = frozenset(
    {
        _S.COMPLETE.value,
        _S.FAILED_RETRYABLE.value,
        _S.FAILED_TERMINAL.value,
        _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value,
        _S.SUPERSEDED.value,
    }
)


class ReconciliationOutcomeType(str, Enum):
    SUBMITTED = "SUBMITTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    UNREACHABLE = "UNREACHABLE"


@dataclass(frozen=True, slots=True)
class ProviderReconciliationOutcome:
    """Provider-independent, non-PHI reconciliation result."""

    outcome: ReconciliationOutcomeType
    provider_job_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ReconciliationOutcomeType):
            object.__setattr__(self, "outcome", ReconciliationOutcomeType(self.outcome))
        if (
            self.provider_job_id is not None
            and self.outcome is not ReconciliationOutcomeType.SUBMITTED
        ):
            raise ProviderJobLifecycleError("ASYNC_PROVIDER_OUTCOME_METADATA_INVALID")
        if self.provider_job_id is not None and not self.provider_job_id.strip():
            raise ProviderJobLifecycleError("ASYNC_PROVIDER_OUTCOME_METADATA_INVALID")


ReconciliationOutcome = ProviderReconciliationOutcome


@dataclass(frozen=True, slots=True)
class ProviderReconciliationClaim:
    """Only durable, non-PHI identity needed by a later provider adapter."""

    provider_attempt_id: uuid.UUID
    job_id: uuid.UUID
    provider_adapter: str
    provider_contract_version: str
    provider_job_id: str | None
    client_request_token_digest: str
    provider_request_fingerprint: str
    claimed_version: int
    reconciliation_attempt_number: int
    reconciliation_deadline_at: datetime | None


class ProviderJobLifecycleError(RuntimeError):
    """Stable value-free lifecycle failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_UNSET: Final = object()
_IMMUTABLE_CREATE_FIELDS = (
    "job_id",
    "tenant_id",
    "patient_id",
    "source_document_id",
    "job_attempt_number",
    "provider_adapter",
    "provider_contract_version",
    "provider_model_version",
    "client_request_token_digest",
    "provider_request_fingerprint",
    "expected_page_count",
)


def _utc(value: datetime | None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _uuid(value: uuid.UUID | str, code: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProviderJobLifecycleError(code) from exc


def _audit_context(tenant_id: uuid.UUID) -> AuditContext:
    return AuditContext.for_tenant(
        tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
    )


def _event_type(target: str) -> str | None:
    return {
        _S.CREATED.value: "EXTRACTION_PROVIDER_ATTEMPT_CREATED",
        _S.SUBMITTING.value: "EXTRACTION_PROVIDER_SUBMISSION_STARTED",
        _S.SUBMITTED.value: "EXTRACTION_PROVIDER_JOB_SUBMITTED",
        _S.LOCAL_WAIT_EXPIRED.value: "EXTRACTION_PROVIDER_LOCAL_WAIT_EXPIRED",
        _S.SUCCEEDED.value: "EXTRACTION_PROVIDER_JOB_SUCCEEDED",
        _S.FAILED_RETRYABLE.value: "EXTRACTION_PROVIDER_JOB_FAILED",
        _S.FAILED_TERMINAL.value: "EXTRACTION_PROVIDER_JOB_FAILED",
        _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value: "EXTRACTION_PROVIDER_RECONCILIATION_EXHAUSTED",
    }.get(target)


async def _required_audit(
    db: AsyncSession,
    *,
    row: ExtractionProviderJobRecord,
    event_type: str,
    suffix: str,
    actor_id: str = "SYSTEM_PROVIDER_JOB",
) -> None:
    try:
        await enqueue_audit_event(
            db,
            audit_context=_audit_context(row.tenant_id),
            idempotency_key=f"provider-job:{row.id}:{suffix}",
            actor_id=actor_id,
            event_type=event_type,
            target_id=str(row.id),
            patient_id=str(row.patient_id),
            metadata={
                "job_id": str(row.job_id),
                "provider_attempt_id": str(row.id),
                "status": row.status,
                "version": row.version,
            },
        )
    except Exception as exc:  # noqa: BLE001 - public error must stay value-free
        await db.rollback()
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_AUDIT_REQUIRED") from exc


def _same_create_metadata(
    row: ExtractionProviderJobRecord, expected: Mapping[str, Any]
) -> bool:
    return all(
        getattr(row, field) == expected[field] for field in _IMMUTABLE_CREATE_FIELDS
    )


async def create_provider_attempt(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID | None = None,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
    job_attempt_number: int,
    provider_adapter: str,
    provider_contract_version: str,
    provider_model_version: str | None,
    client_request_token_digest: str,
    provider_request_fingerprint: str,
    expected_page_count: int | None = None,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    """Create one durable attempt, or replay an exact logical create."""

    job_uuid = _uuid(job_id, "ASYNC_PROVIDER_JOB_NOT_FOUND")
    tenant_uuid = _uuid(tenant_id, "ASYNC_PROVIDER_TENANT_BINDING_MISMATCH")
    patient_uuid = _uuid(patient_id, "ASYNC_PROVIDER_PATIENT_BINDING_MISMATCH")
    document_uuid = _uuid(
        source_document_id, "ASYNC_PROVIDER_DOCUMENT_BINDING_MISMATCH"
    )
    if job_attempt_number < 1:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_ATTEMPT_NUMBER_INVALID")
    if expected_page_count is not None and (
        not isinstance(expected_page_count, int) or expected_page_count <= 0
    ):
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_PAGE_COUNT_INVALID")
    required_text = (
        provider_adapter,
        provider_contract_version,
        client_request_token_digest,
        provider_request_fingerprint,
    )
    if any(not isinstance(value, str) or not value.strip() for value in required_text):
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_METADATA_INVALID")
    job = (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_uuid).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_JOB_NOT_FOUND")
    if job.tenant_id is None:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_TENANT_REQUIRED")
    if (
        job.tenant_id != tenant_uuid
        or job.patient_id != patient_uuid
        or job.document_id != document_uuid
    ):
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_GRAPH_BINDING_MISMATCH")

    expected = {
        "job_id": job_uuid,
        "tenant_id": tenant_uuid,
        "patient_id": patient_uuid,
        "source_document_id": document_uuid,
        "job_attempt_number": job_attempt_number,
        "provider_adapter": provider_adapter,
        "provider_contract_version": provider_contract_version,
        "provider_model_version": provider_model_version,
        "client_request_token_digest": client_request_token_digest,
        "provider_request_fingerprint": provider_request_fingerprint,
        "expected_page_count": expected_page_count,
    }
    existing = (
        await db.execute(
            select(ExtractionProviderJobRecord)
            .where(
                ExtractionProviderJobRecord.job_id == job_uuid,
                ExtractionProviderJobRecord.job_attempt_number == job_attempt_number,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not _same_create_metadata(existing, expected):
            raise ProviderJobLifecycleError("ASYNC_PROVIDER_ATTEMPT_IDENTITY_COLLISION")
        return existing

    now = _utc(occurred_at)
    row = ExtractionProviderJobRecord(
        id=provider_attempt_id or uuid.uuid4(),
        **expected,
        status=_S.CREATED.value,
        version=1,
        response_complete=False,
        result_retrieval_complete=False,
        reconciliation_attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    # The authoritative job row lock serializes normal replays.  Integrity
    # failures not covered by the explicit replay check remain database errors;
    # they are never misreported as a successful replay.
    await db.flush()
    await _required_audit(
        db, row=row, event_type="EXTRACTION_PROVIDER_ATTEMPT_CREATED", suffix="created"
    )
    return row


def _metadata_matches(
    row: ExtractionProviderJobRecord,
    *,
    provider_job_id: object = _UNSET,
    provider_completed_at: object = _UNSET,
    response_complete: object = _UNSET,
    result_retrieval_complete: object = _UNSET,
    expected_page_count: object = _UNSET,
    observed_page_count: object = _UNSET,
) -> bool:
    checks = {
        "provider_job_id": provider_job_id,
        "provider_completed_at": provider_completed_at,
        "response_complete": response_complete,
        "result_retrieval_complete": result_retrieval_complete,
        "expected_page_count": expected_page_count,
        "observed_page_count": observed_page_count,
    }
    return all(
        value is _UNSET or getattr(row, field) == value
        for field, value in checks.items()
    )


async def _lock_attempt(
    db: AsyncSession, provider_attempt_id: uuid.UUID | str
) -> ExtractionProviderJobRecord:
    row = (
        await db.execute(
            select(ExtractionProviderJobRecord)
            .where(
                ExtractionProviderJobRecord.id
                == _uuid(provider_attempt_id, "ASYNC_PROVIDER_ATTEMPT_NOT_FOUND")
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_ATTEMPT_NOT_FOUND")
    return row


async def _transition_locked(
    db: AsyncSession,
    *,
    row: ExtractionProviderJobRecord,
    target: str,
    expected_version: int,
    now: datetime,
    audit: bool = True,
    force_same_state_mutation: bool = False,
    provider_started_at: object = _UNSET,
    provider_job_id: object = _UNSET,
    provider_completed_at: object = _UNSET,
    response_complete: object = _UNSET,
    result_retrieval_complete: object = _UNSET,
    expected_page_count: object = _UNSET,
    observed_page_count: object = _UNSET,
    reconciliation_deadline_at: object = _UNSET,
    last_reconciled_at: object = _UNSET,
    next_reconcile_at: object = _UNSET,
    increment_reconciliation: bool = False,
) -> ExtractionProviderJobRecord:
    target = target.value if isinstance(target, ProviderJobStatus) else str(target)
    if target not in LEGAL_PROVIDER_JOB_TRANSITIONS:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_STATE_INVALID")
    if expected_version < 1 or row.version != expected_version:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_VERSION_CONFLICT")
    if (
        provider_job_id is not _UNSET
        and row.provider_job_id is not None
        and provider_job_id != row.provider_job_id
    ):
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_JOB_ID_CONFLICT")
    if row.status == target and not force_same_state_mutation:
        if _metadata_matches(
            row,
            provider_job_id=provider_job_id,
            provider_completed_at=provider_completed_at,
            response_complete=response_complete,
            result_retrieval_complete=result_retrieval_complete,
            expected_page_count=expected_page_count,
            observed_page_count=observed_page_count,
        ):
            return row
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_TRANSITION_METADATA_COLLISION")
    if target not in LEGAL_PROVIDER_JOB_TRANSITIONS.get(
        row.status, frozenset()
    ) and not (force_same_state_mutation and row.status == target):
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_ILLEGAL_TRANSITION")
    if target == _S.SUBMITTED.value:
        candidate = (
            row.provider_job_id if provider_job_id is _UNSET else provider_job_id
        )
        if not isinstance(candidate, str) or not candidate.strip():
            raise ProviderJobLifecycleError("ASYNC_PROVIDER_ID_REQUIRED")
    if target == _S.SUCCEEDED.value and not row.provider_job_id:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_ID_REQUIRED")
    if target == _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value:
        response_complete = False
        result_retrieval_complete = False
    row.status = target
    if provider_started_at is not _UNSET:
        row.provider_started_at = provider_started_at
    elif target == _S.SUBMITTING.value and row.provider_started_at is None:
        row.provider_started_at = now
    if provider_job_id is not _UNSET:
        row.provider_job_id = provider_job_id
    if provider_completed_at is not _UNSET:
        row.provider_completed_at = provider_completed_at
    elif target == _S.SUCCEEDED.value and row.provider_completed_at is None:
        row.provider_completed_at = now
    if response_complete is not _UNSET:
        row.response_complete = response_complete
    if result_retrieval_complete is not _UNSET:
        row.result_retrieval_complete = result_retrieval_complete
    if expected_page_count is not _UNSET:
        row.expected_page_count = expected_page_count
    if observed_page_count is not _UNSET:
        row.observed_page_count = observed_page_count
    if reconciliation_deadline_at is not _UNSET:
        row.reconciliation_deadline_at = reconciliation_deadline_at
    if last_reconciled_at is not _UNSET:
        row.last_reconciled_at = last_reconciled_at
    if next_reconcile_at is not _UNSET:
        row.next_reconcile_at = next_reconcile_at
    if increment_reconciliation:
        row.reconciliation_attempt_count += 1
    row.version += 1
    row.updated_at = now
    await db.flush()
    event_type = _event_type(target)
    if audit and event_type is not None:
        await _required_audit(
            db,
            row=row,
            event_type=event_type,
            suffix=f"status:{row.version}",
        )
    return row


async def transition_provider_attempt(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    expected_version: int,
    target_status: ProviderJobStatus | str,
    provider_job_id: str | None | object = _UNSET,
    provider_completed_at: datetime | None | object = _UNSET,
    response_complete: bool | object = _UNSET,
    result_retrieval_complete: bool | object = _UNSET,
    expected_page_count: int | None | object = _UNSET,
    observed_page_count: int | None | object = _UNSET,
    provider_started_at: datetime | None | object = _UNSET,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    """Apply one locked, version-checked material transition."""

    row = await _lock_attempt(db, provider_attempt_id)
    return await _transition_locked(
        db,
        row=row,
        target=target_status.value
        if isinstance(target_status, ProviderJobStatus)
        else target_status,
        expected_version=expected_version,
        now=_utc(occurred_at),
        provider_job_id=provider_job_id,
        provider_completed_at=provider_completed_at,
        response_complete=response_complete,
        result_retrieval_complete=result_retrieval_complete,
        expected_page_count=expected_page_count,
        observed_page_count=observed_page_count,
        provider_started_at=provider_started_at,
    )


async def begin_provider_submission(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    expected_version: int,
    expected_page_count: int | None | object = _UNSET,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    kwargs: dict[str, Any] = {}
    if expected_page_count is not _UNSET:
        kwargs["expected_page_count"] = expected_page_count
    return await transition_provider_attempt(
        db,
        provider_attempt_id=provider_attempt_id,
        expected_version=expected_version,
        target_status=_S.SUBMITTING,
        provider_started_at=_utc(occurred_at),
        occurred_at=occurred_at,
        **kwargs,
    )


async def bind_submitted_provider_job(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    expected_version: int,
    provider_job_id: str,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    return await transition_provider_attempt(
        db,
        provider_attempt_id=provider_attempt_id,
        expected_version=expected_version,
        target_status=_S.SUBMITTED,
        provider_job_id=provider_job_id,
        occurred_at=occurred_at,
    )


async def mark_provider_in_progress(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    expected_version: int,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    return await transition_provider_attempt(
        db,
        provider_attempt_id=provider_attempt_id,
        expected_version=expected_version,
        target_status=_S.IN_PROGRESS,
        occurred_at=occurred_at,
    )


async def mark_provider_succeeded(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    expected_version: int,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    return await transition_provider_attempt(
        db,
        provider_attempt_id=provider_attempt_id,
        expected_version=expected_version,
        target_status=_S.SUCCEEDED,
        occurred_at=occurred_at,
    )


async def mark_provider_failed(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    expected_version: int,
    retryable: bool,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    return await transition_provider_attempt(
        db,
        provider_attempt_id=provider_attempt_id,
        expected_version=expected_version,
        target_status=_S.FAILED_RETRYABLE if retryable else _S.FAILED_TERMINAL,
        occurred_at=occurred_at,
    )


async def mark_local_wait_expired(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    expected_version: int,
    reconciliation_window_seconds: int = 900,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    if not 60 <= reconciliation_window_seconds <= 86400:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_RECONCILIATION_WINDOW_INVALID")
    row = await _lock_attempt(db, provider_attempt_id)
    now = _utc(occurred_at)
    if row.status == _S.LOCAL_WAIT_EXPIRED.value:
        return row
    deadline = row.reconciliation_deadline_at or (
        now + timedelta(seconds=reconciliation_window_seconds)
    )
    return await _transition_locked(
        db,
        row=row,
        target=_S.LOCAL_WAIT_EXPIRED.value,
        expected_version=expected_version,
        now=now,
        reconciliation_deadline_at=deadline,
        next_reconcile_at=row.next_reconcile_at or now,
    )


def _claim_from(row: ExtractionProviderJobRecord) -> ProviderReconciliationClaim:
    return ProviderReconciliationClaim(
        provider_attempt_id=row.id,
        job_id=row.job_id,
        provider_adapter=row.provider_adapter,
        provider_contract_version=row.provider_contract_version,
        provider_job_id=row.provider_job_id,
        client_request_token_digest=row.client_request_token_digest,
        provider_request_fingerprint=row.provider_request_fingerprint,
        claimed_version=row.version,
        reconciliation_attempt_number=row.reconciliation_attempt_count,
        reconciliation_deadline_at=row.reconciliation_deadline_at,
    )


async def claim_due_provider_reconciliations(
    db: AsyncSession,
    *,
    max_attempts: int = 3,
    window_seconds: int = 900,
    interval_seconds: int = 2,
    batch_size: int = 25,
    now: datetime | None = None,
) -> list[ProviderReconciliationClaim]:
    """Claim due rows with PostgreSQL ``FOR UPDATE SKIP LOCKED``."""

    if not 1 <= max_attempts <= 5:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_RECONCILIATION_MAX_INVALID")
    if not 60 <= window_seconds <= 86400:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_RECONCILIATION_WINDOW_INVALID")
    if not 1 <= interval_seconds <= 60:
        raise ProviderJobLifecycleError(
            "ASYNC_PROVIDER_RECONCILIATION_INTERVAL_INVALID"
        )
    if not 1 <= batch_size <= 100:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_RECONCILIATION_BATCH_INVALID")
    evaluated_at = _utc(now)
    rows = (
        (
            await db.execute(
                select(ExtractionProviderJobRecord)
                .where(
                    ExtractionProviderJobRecord.status.in_(
                        [_S.LOCAL_WAIT_EXPIRED.value, _S.RECONCILING.value]
                    ),
                    or_(
                        ExtractionProviderJobRecord.next_reconcile_at.is_(None),
                        ExtractionProviderJobRecord.next_reconcile_at <= evaluated_at,
                    ),
                )
                .order_by(
                    func.coalesce(
                        ExtractionProviderJobRecord.next_reconcile_at, evaluated_at
                    ),
                    ExtractionProviderJobRecord.id,
                )
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
        )
        .scalars()
        .all()
    )
    claims: list[ProviderReconciliationClaim] = []
    for row in rows:
        if row.reconciliation_attempt_count >= max_attempts or (
            row.reconciliation_deadline_at is not None
            and row.reconciliation_deadline_at <= evaluated_at
        ):
            await _transition_locked(
                db,
                row=row,
                target=_S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value,
                expected_version=row.version,
                now=evaluated_at,
            )
            continue
        if row.reconciliation_deadline_at is None:
            row.reconciliation_deadline_at = evaluated_at + timedelta(
                seconds=window_seconds
            )
        await _transition_locked(
            db,
            row=row,
            target=_S.RECONCILING.value,
            expected_version=row.version,
            now=evaluated_at,
            audit=False,
            force_same_state_mutation=True,
            last_reconciled_at=evaluated_at,
            next_reconcile_at=evaluated_at + timedelta(seconds=interval_seconds),
            increment_reconciliation=True,
        )
        claims.append(_claim_from(row))
    await db.flush()
    return claims


async def apply_reconciliation_outcome(
    db: AsyncSession,
    *,
    claim: ProviderReconciliationClaim,
    outcome: ProviderReconciliationOutcome,
    max_attempts: int = 3,
    interval_seconds: int = 2,
    occurred_at: datetime | None = None,
) -> ExtractionProviderJobRecord:
    """Apply a callback result only when the claim version is still current."""

    if not 1 <= max_attempts <= 5 or not 1 <= interval_seconds <= 60:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_RECONCILIATION_CONFIG_INVALID")
    if not isinstance(outcome, ProviderReconciliationOutcome):
        outcome = ProviderReconciliationOutcome(outcome)
    row = await _lock_attempt(db, claim.provider_attempt_id)
    if row.version != claim.claimed_version or row.status != _S.RECONCILING.value:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_STALE_RECONCILIATION_CLAIM")
    now = _utc(occurred_at)
    kind = outcome.outcome
    if kind is ReconciliationOutcomeType.SUBMITTED:
        return await _transition_locked(
            db,
            row=row,
            target=_S.SUBMITTED.value,
            expected_version=claim.claimed_version,
            now=now,
            provider_job_id=outcome.provider_job_id,
        )
    if kind is ReconciliationOutcomeType.SUCCEEDED:
        return await _transition_locked(
            db,
            row=row,
            target=_S.SUCCEEDED.value,
            expected_version=claim.claimed_version,
            now=now,
        )
    if kind is ReconciliationOutcomeType.FAILED_RETRYABLE:
        return await _transition_locked(
            db,
            row=row,
            target=_S.FAILED_RETRYABLE.value,
            expected_version=claim.claimed_version,
            now=now,
        )
    if kind is ReconciliationOutcomeType.FAILED_TERMINAL:
        return await _transition_locked(
            db,
            row=row,
            target=_S.FAILED_TERMINAL.value,
            expected_version=claim.claimed_version,
            now=now,
        )
    if kind is ReconciliationOutcomeType.IN_PROGRESS:
        target = _S.RECONCILING.value
    else:
        target = _S.RECONCILING.value
    exhausted = row.reconciliation_attempt_count >= max_attempts or (
        row.reconciliation_deadline_at is not None
        and row.reconciliation_deadline_at <= now
    )
    if kind is ReconciliationOutcomeType.UNREACHABLE and exhausted:
        target = _S.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value
    return await _transition_locked(
        db,
        row=row,
        target=target,
        expected_version=claim.claimed_version,
        now=now,
        audit=target != _S.RECONCILING.value,
        force_same_state_mutation=target == _S.RECONCILING.value,
        next_reconcile_at=now + timedelta(seconds=interval_seconds),
    )


# Explicit aliases keep the service API readable at call sites and make the
# durable operation names stable for later A3 adapter integration.
begin_submission = begin_provider_submission
bind_submitted_job = bind_submitted_provider_job
mark_in_progress = mark_provider_in_progress
mark_succeeded = mark_provider_succeeded
