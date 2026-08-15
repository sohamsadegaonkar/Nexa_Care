"""Value-free, immutable persistence for provider subattempt provenance."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_models import ProviderAttemptTrace
from app.models.pipeline import ExtractionAttemptEventRecord, ExtractionJob


_EVENT_NAMESPACE = uuid.UUID("b3194c7a-bfdf-4d01-9e6d-13ef3f8d868c")
_EVENT_VERSION = "nexa-extraction-attempt-event:v1"
_IMMUTABLE_FIELDS = (
    "id",
    "tenant_id",
    "patient_id",
    "job_id",
    "source_document_id",
    "job_attempt_number",
    "provider_subattempt_number",
    "provider_adapter",
    "provider_contract_version",
    "provider_model_version",
    "outcome",
    "error_code",
    "response_complete",
    "occurred_at",
)


class ExtractionAttemptEventCollision(RuntimeError):
    """A deterministic provider-attempt identity conflicts with different data."""


def event_id_for(
    *, job_id: uuid.UUID, job_attempt_number: int, provider_subattempt_number: int
) -> uuid.UUID:
    return uuid.uuid5(
        _EVENT_NAMESPACE,
        ":".join(
            (
                _EVENT_VERSION,
                str(job_id),
                str(job_attempt_number),
                str(provider_subattempt_number),
            )
        ),
    )


def _is_expected_unique_violation(exc: BaseException) -> bool:
    pending: list[BaseException | None] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        diagnostic = getattr(current, "diag", None)
        sqlstate = next(
            (
                getattr(current, name, None)
                for name in ("sqlstate", "pgcode", "code")
                if getattr(current, name, None) is not None
            ),
            None,
        )
        constraint = getattr(current, "constraint_name", None) or getattr(
            diagnostic, "constraint_name", None
        )
        if sqlstate == "23505" and constraint in {
            "extraction_attempt_events_pkey",
            "uq_extraction_attempt_events_logical_identity",
        }:
            return True
        pending.extend(
            getattr(current, name, None)
            for name in ("orig", "__cause__", "__context__")
        )
    return False


def _same_event(
    existing: ExtractionAttemptEventRecord, expected: ExtractionAttemptEventRecord
) -> bool:
    return all(
        getattr(existing, field) == getattr(expected, field)
        for field in _IMMUTABLE_FIELDS
    )


async def persist_provider_attempt_events(
    db: AsyncSession,
    *,
    job: ExtractionJob,
    traces: tuple[ProviderAttemptTrace, ...],
) -> None:
    """Persist exact provider call outcomes or fail closed on a collision.

    The caller controls transaction boundaries.  No exception objects, provider
    bodies, source text, or clinical values enter this table.
    """

    for trace in traces:
        expected = ExtractionAttemptEventRecord(
            id=event_id_for(
                job_id=job.id,
                job_attempt_number=job.attempt_count,
                provider_subattempt_number=trace.provider_subattempt_number,
            ),
            tenant_id=job.tenant_id,
            patient_id=job.patient_id,
            job_id=job.id,
            source_document_id=job.document_id,
            job_attempt_number=job.attempt_count,
            provider_subattempt_number=trace.provider_subattempt_number,
            provider_adapter=trace.provider_adapter,
            provider_contract_version=trace.provider_contract_version,
            provider_model_version=trace.provider_model_version,
            outcome=trace.outcome.value,
            error_code=trace.error_code,
            response_complete=trace.response_complete,
            occurred_at=trace.occurred_at,
        )
        try:
            async with db.begin_nested():
                db.add(expected)
                await db.flush()
        except IntegrityError as exc:
            if not _is_expected_unique_violation(exc):
                raise
            existing = (
                await db.execute(
                    select(ExtractionAttemptEventRecord)
                    .where(
                        ExtractionAttemptEventRecord.job_id == job.id,
                        ExtractionAttemptEventRecord.job_attempt_number
                        == job.attempt_count,
                        ExtractionAttemptEventRecord.provider_subattempt_number
                        == trace.provider_subattempt_number,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is None or not _same_event(existing, expected):
                raise ExtractionAttemptEventCollision(
                    "EXTRACTION_ATTEMPT_EVENT_COLLISION"
                ) from exc
