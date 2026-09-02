"""Feature-gated orchestration around the A2 lifecycle and A3/A4 adapter.

AWS-specific work lives here; ``provider_job_reconciliation_processor`` stays
provider independent and only applies its typed outcome.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.async_textract import (
    ASYNC_TEXTRACT_CONTRACT_VERSION,
    AsyncTextractProvider,
    ControlledS3Location,
)
from app.ai.extractor import (
    DocumentExtractionError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.models.pipeline import ExtractionJob, ExtractionProviderJobRecord
from app.core.document_processing_gate import (
    DelegatedClinicalTrustError,
    quarantine_delegated_clinical_trust_denial,
    recheck_delegated_document_processing_trust,
)
from app.services.provider_job_lifecycle import (
    ProviderJobLifecycleError,
    ProviderJobStatus,
    ProviderReconciliationClaim,
    ProviderReconciliationOutcome,
    ReconciliationOutcomeType,
    begin_provider_submission,
    bind_submitted_provider_job,
    create_provider_attempt,
    mark_local_wait_expired,
    transition_provider_attempt,
)
from app.services.textract_source_staging import (
    PreparedTextractSource,
    TextractSourceStager,
    TextractSourceStagingError,
)

logger = logging.getLogger("nexa_logger")


def async_multipage_eligible(
    *, provider: str, enabled: bool, content_type: str, storage_ref: str
) -> bool:
    """Narrow, fail-closed selection gate for the asynchronous pilot path."""

    return (
        provider == "aws_textract"
        and enabled is True
        and isinstance(content_type, str)
        and content_type.lower().split(";", 1)[0].strip() == "application/pdf"
        and isinstance(storage_ref, str)
        and storage_ref.startswith("s3://")
    )


def deterministic_client_request_token(provider_attempt_id: uuid.UUID | str) -> str:
    """Return a bounded token that can be reconstructed after a lost response."""

    try:
        attempt = (
            provider_attempt_id
            if isinstance(provider_attempt_id, uuid.UUID)
            else uuid.UUID(str(provider_attempt_id))
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_ATTEMPT_NOT_FOUND") from exc
    return f"nexa-{attempt.hex}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def provider_request_fingerprint(
    *,
    source_document_id: uuid.UUID | str,
    source_content_hash: str,
    provider_adapter: str = "aws_textract",
    provider_contract_version: str = ASYNC_TEXTRACT_CONTRACT_VERSION,
    staging_bucket: str,
    staging_key: str,
    expected_page_count: int,
) -> str:
    """Canonical, value-free identity of the async provider request."""

    if not isinstance(source_content_hash, str) or len(source_content_hash) != 64:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_SOURCE_FINGERPRINT_INVALID")
    payload = {
        "adapter": provider_adapter,
        "contract": provider_contract_version,
        "document_id": str(source_document_id),
        "expected_page_count": expected_page_count,
        "features": ["QUERIES", "FORMS", "TABLES"],
        "query_contract": "pilot-v1",
        "source_sha256": source_content_hash.lower(),
        "staging_bucket": staging_bucket,
        "staging_key": staging_key,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _sha256_text(canonical)


@dataclass(frozen=True, slots=True)
class AsyncStartResult:
    provider_attempt_id: uuid.UUID
    provider_job_id: str | None
    status: str
    expected_page_count: int


BeforeProviderSubmissionGuard = Callable[[], Awaitable[None]]
BeforeSourceRetrievalGuard = Callable[[], Awaitable[None]]


async def _delete_staged_source_without_replacing_denial(
    stager: TextractSourceStager,
    *,
    tenant_id: uuid.UUID,
    provider_attempt_id: uuid.UUID,
) -> None:
    """Best-effort cleanup that never turns a security denial into a retry."""

    try:
        await stager.delete(
            tenant_id=tenant_id, provider_attempt_id=provider_attempt_id
        )
    except Exception:  # noqa: BLE001 - cleanup is not an authorization decision
        logger.warning("delegated_staging_cleanup_failed")


async def _mark_pre_submission_denial(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    stager: TextractSourceStager,
) -> None:
    """Terminally stop an unsubmitted attempt and discard its staged source."""

    current = (
        await db.execute(
            select(ExtractionProviderJobRecord)
            .where(ExtractionProviderJobRecord.id == provider_attempt_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if current is not None and current.status == ProviderJobStatus.SUBMITTING.value:
        await transition_provider_attempt(
            db,
            provider_attempt_id=current.id,
            expected_version=current.version,
            target_status=ProviderJobStatus.FAILED_TERMINAL,
        )
        await db.commit()
    if current is not None:
        await _delete_staged_source_without_replacing_denial(
            stager, tenant_id=current.tenant_id, provider_attempt_id=current.id
        )


async def _recheck_attempt_trust(
    db: AsyncSession, *, provider_attempt_id: uuid.UUID
) -> ExtractionJob:
    """Load the authoritative workflow rather than trusting lifecycle claims."""

    attempt = (
        await db.execute(
            select(ExtractionProviderJobRecord).where(
                ExtractionProviderJobRecord.id == provider_attempt_id
            )
        )
    ).scalar_one_or_none()
    job = (
        await db.execute(
            select(ExtractionJob).where(
                ExtractionJob.id == (attempt.job_id if attempt is not None else None)
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise DelegatedClinicalTrustError("DELEGATED_CLINICAL_WORKFLOW_UNAVAILABLE")
    await recheck_delegated_document_processing_trust(job=job, db=db)
    return job


async def _quarantine_retrieval_denial(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    job_id: uuid.UUID,
    error_code: str,
    stager: TextractSourceStager,
) -> None:
    """Persist only value-free lifecycle and denial state after trust loss."""

    await quarantine_delegated_clinical_trust_denial(
        db=db, job_id=job_id, error_code=error_code
    )
    current = (
        await db.execute(
            select(ExtractionProviderJobRecord)
            .where(ExtractionProviderJobRecord.id == provider_attempt_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if current is not None and current.status in {
        ProviderJobStatus.SUCCEEDED.value,
        ProviderJobStatus.FETCHING_RESULTS.value,
        ProviderJobStatus.VALIDATING_COMPLETE_RESULT.value,
    }:
        await transition_provider_attempt(
            db,
            provider_attempt_id=current.id,
            expected_version=current.version,
            target_status=ProviderJobStatus.FAILED_TERMINAL,
        )
        await db.commit()
    if current is not None:
        await _delete_staged_source_without_replacing_denial(
            stager, tenant_id=current.tenant_id, provider_attempt_id=current.id
        )


async def prepare_and_create_provider_attempt(
    db: AsyncSession,
    *,
    stager: TextractSourceStager,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
    job_attempt_number: int,
    before_source_retrieval: BeforeSourceRetrievalGuard,
    occurred_at: datetime | None = None,
) -> tuple[ExtractionProviderJobRecord, PreparedTextractSource]:
    """Create the durable attempt only after independent source preflight."""

    existing = (
        await db.execute(
            select(ExtractionProviderJobRecord).where(
                ExtractionProviderJobRecord.job_id == job_id,
                ExtractionProviderJobRecord.job_attempt_number == job_attempt_number,
            )
        )
    ).scalar_one_or_none()
    attempt_id = existing.id if existing is not None else uuid.uuid4()
    # ``prepare_for_graph`` decrypts/reads the source.  The caller must supply
    # the current delegated decision immediately before that PHI boundary.
    await before_source_retrieval()
    prepared = await stager.prepare_for_graph(
        db,
        provider_attempt_id=attempt_id,
        job_id=job_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        source_document_id=source_document_id,
    )
    fingerprint = provider_request_fingerprint(
        source_document_id=prepared.source_document_id,
        source_content_hash=prepared.content_hash,
        staging_bucket=prepared.bucket,
        staging_key=prepared.key,
        expected_page_count=prepared.page_count,
    )
    token = deterministic_client_request_token(attempt_id)
    row = await create_provider_attempt(
        db,
        provider_attempt_id=attempt_id,
        job_id=job_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        source_document_id=source_document_id,
        job_attempt_number=job_attempt_number,
        provider_adapter="aws_textract",
        provider_contract_version=ASYNC_TEXTRACT_CONTRACT_VERSION,
        provider_model_version=None,
        client_request_token_digest=_sha256_text(token),
        provider_request_fingerprint=fingerprint,
        expected_page_count=prepared.page_count,
        occurred_at=occurred_at,
    )
    return row, prepared


async def start_async_provider_attempt(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    prepared: PreparedTextractSource,
    stager: TextractSourceStager,
    provider: AsyncTextractProvider,
    before_provider_submission: BeforeProviderSubmissionGuard,
) -> AsyncStartResult:
    """Preflight, stage, start, and bind one immutable provider attempt."""

    row = (
        await db.execute(
            select(ExtractionProviderJobRecord)
            .where(ExtractionProviderJobRecord.id == provider_attempt_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_ATTEMPT_NOT_FOUND")
    expected_fp = provider_request_fingerprint(
        source_document_id=prepared.source_document_id,
        source_content_hash=prepared.content_hash,
        staging_bucket=prepared.bucket,
        staging_key=prepared.key,
        expected_page_count=prepared.page_count,
    )
    if not hmac.compare_digest(row.provider_request_fingerprint, expected_fp):
        raise TextractSourceStagingError("ASYNC_PROVIDER_REQUEST_FINGERPRINT_MISMATCH")
    token = deterministic_client_request_token(row.id)
    if not hmac.compare_digest(row.client_request_token_digest, _sha256_text(token)):
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_CLIENT_TOKEN_MISMATCH")
    if row.provider_job_id is not None:
        return AsyncStartResult(
            row.id, row.provider_job_id, row.status, prepared.page_count
        )
    if row.status == ProviderJobStatus.CREATED.value:
        row = await begin_provider_submission(
            db,
            provider_attempt_id=row.id,
            expected_version=row.version,
            expected_page_count=prepared.page_count,
        )
        await db.commit()
    try:
        staged = await stager.stage(prepared)
    except TextractSourceStagingError:
        current = (
            await db.execute(
                select(ExtractionProviderJobRecord)
                .where(ExtractionProviderJobRecord.id == row.id)
                .with_for_update()
            )
        ).scalar_one()
        if current.status == ProviderJobStatus.SUBMITTING.value:
            await transition_provider_attempt(
                db,
                provider_attempt_id=current.id,
                expected_version=current.version,
                target_status=ProviderJobStatus.FAILED_TERMINAL,
            )
            await db.commit()
        raise
    try:
        # Staging itself may have awaited and must never authorize submission.
        await before_provider_submission()
    except Exception:
        await _mark_pre_submission_denial(
            db,
            provider_attempt_id=row.id,
            stager=stager,
        )
        raise
    try:
        started = await provider.start(
            location=ControlledS3Location(staged.bucket, staged.key),
            client_request_token=token,
            provider_request_fingerprint=row.provider_request_fingerprint,
            provider_attempt_id=str(row.id),
        )
    except ProviderTimeoutError:
        current = (
            await db.execute(
                select(ExtractionProviderJobRecord)
                .where(ExtractionProviderJobRecord.id == row.id)
                .with_for_update()
            )
        ).scalar_one()
        if current.status == ProviderJobStatus.SUBMITTING.value:
            await mark_local_wait_expired(
                db, provider_attempt_id=current.id, expected_version=current.version
            )
            await db.commit()
        return AsyncStartResult(
            row.id,
            None,
            ProviderJobStatus.LOCAL_WAIT_EXPIRED.value,
            prepared.page_count,
        )
    except DocumentExtractionError as exc:
        current = (
            await db.execute(
                select(ExtractionProviderJobRecord)
                .where(ExtractionProviderJobRecord.id == row.id)
                .with_for_update()
            )
        ).scalar_one()
        if current.status == ProviderJobStatus.SUBMITTING.value:
            await transition_provider_attempt(
                db,
                provider_attempt_id=current.id,
                expected_version=current.version,
                target_status=(
                    ProviderJobStatus.FAILED_RETRYABLE
                    if getattr(exc, "retryable", False)
                    else ProviderJobStatus.FAILED_TERMINAL
                ),
            )
            await db.commit()
        raise
    row = await bind_submitted_provider_job(
        db,
        provider_attempt_id=row.id,
        expected_version=row.version,
        provider_job_id=started.provider_job_id,
    )
    await db.commit()
    return AsyncStartResult(
        row.id, row.provider_job_id, row.status, prepared.page_count
    )


def make_textract_reconciliation_callback(
    *,
    session_factory: Any,
    provider: AsyncTextractProvider,
    stager: TextractSourceStager,
):
    """Return an A2 callback that never mutates lifecycle rows directly."""

    async def callback(
        claim: ProviderReconciliationClaim,
    ) -> ProviderReconciliationOutcome:
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(ExtractionProviderJobRecord).where(
                        ExtractionProviderJobRecord.id == claim.provider_attempt_id
                    )
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.provider_request_fingerprint
                != claim.provider_request_fingerprint
            ):
                raise TextractSourceStagingError(
                    "ASYNC_PROVIDER_REQUEST_FINGERPRINT_MISMATCH"
                )
            job = (
                await db.execute(
                    select(ExtractionJob).where(ExtractionJob.id == claim.job_id)
                )
            ).scalar_one_or_none()
            if job is None or job.id != row.job_id:
                raise TextractSourceStagingError(
                    "ASYNC_PROVIDER_GRAPH_BINDING_MISMATCH"
                )
            try:
                await recheck_delegated_document_processing_trust(job=job, db=db)
                if row.provider_job_id:
                    return await provider.check_status(
                        provider_job_id=row.provider_job_id
                    )
                # This source preparation reads/decrypts PHI.  It follows the
                # preceding current trust/consent/workflow-state checkpoint.
                prepared = await stager.prepare_for_attempt(
                    db, provider_attempt_id=row.id
                )
                expected_fp = provider_request_fingerprint(
                    source_document_id=prepared.source_document_id,
                    source_content_hash=prepared.content_hash,
                    staging_bucket=prepared.bucket,
                    staging_key=prepared.key,
                    expected_page_count=prepared.page_count,
                )
                if not hmac.compare_digest(
                    expected_fp, row.provider_request_fingerprint
                ):
                    raise TextractSourceStagingError(
                        "ASYNC_PROVIDER_REQUEST_FINGERPRINT_MISMATCH"
                    )
                token = deterministic_client_request_token(row.id)
                staged = await stager.stage(prepared)
                # Staging may await, so authorize once more immediately before
                # provider submission.
                await recheck_delegated_document_processing_trust(job=job, db=db)
                started = await provider.start(
                    location=ControlledS3Location(staged.bucket, staged.key),
                    client_request_token=token,
                    provider_request_fingerprint=row.provider_request_fingerprint,
                    provider_attempt_id=str(row.id),
                )
            except DelegatedClinicalTrustError as exc:
                await quarantine_delegated_clinical_trust_denial(
                    db=db, job_id=job.id, error_code=exc.code
                )
                await _delete_staged_source_without_replacing_denial(
                    stager, tenant_id=row.tenant_id, provider_attempt_id=row.id
                )
                return ProviderReconciliationOutcome(
                    ReconciliationOutcomeType.FAILED_TERMINAL
                )
            except (DocumentExtractionError, TextractSourceStagingError):
                raise
            return ProviderReconciliationOutcome(
                ReconciliationOutcomeType.SUBMITTED,
                provider_job_id=started.provider_job_id,
            )

    return callback


async def retrieve_and_complete_provider_attempt(
    db: AsyncSession,
    *,
    provider_attempt_id: uuid.UUID,
    provider: AsyncTextractProvider,
    stager: TextractSourceStager,
) -> Any:
    """Retrieve all pages, clean staging, then mark the attempt COMPLETE.

    The returned document is handed to the caller only after cleanup succeeds;
    no candidate/evidence write occurs in this operational function.
    """

    row = (
        await db.execute(
            select(ExtractionProviderJobRecord)
            .where(ExtractionProviderJobRecord.id == provider_attempt_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status != ProviderJobStatus.SUCCEEDED.value:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_RESULT_NOT_READY")
    if row.provider_job_id is None or row.expected_page_count is None:
        raise ProviderJobLifecycleError("ASYNC_PROVIDER_RESULT_METADATA_INVALID")
    try:
        await _recheck_attempt_trust(db, provider_attempt_id=row.id)
    except DelegatedClinicalTrustError as exc:
        await _quarantine_retrieval_denial(
            db,
            provider_attempt_id=row.id,
            job_id=row.job_id,
            error_code=exc.code,
            stager=stager,
        )
        raise
    row = await transition_provider_attempt(
        db,
        provider_attempt_id=row.id,
        expected_version=row.version,
        target_status=ProviderJobStatus.FETCHING_RESULTS,
    )
    await db.commit()
    try:
        result = await provider.retrieve_complete_result(
            provider_job_id=row.provider_job_id,
            expected_page_count=row.expected_page_count,
        )
        try:
            await _recheck_attempt_trust(db, provider_attempt_id=row.id)
        except DelegatedClinicalTrustError as exc:
            await _quarantine_retrieval_denial(
                db,
                provider_attempt_id=row.id,
                job_id=row.job_id,
                error_code=exc.code,
                stager=stager,
            )
            raise
        observed = provider.last_observed_page_count
        if observed != row.expected_page_count:
            raise ProviderResponseError("Async Textract page completeness failed")
        row = await transition_provider_attempt(
            db,
            provider_attempt_id=row.id,
            expected_version=row.version,
            target_status=ProviderJobStatus.VALIDATING_COMPLETE_RESULT,
            response_complete=True,
            observed_page_count=observed,
        )
        # Cleanup is deliberately before the durable COMPLETE transition and
        # before any downstream candidate handoff.
        await stager.delete(tenant_id=row.tenant_id, provider_attempt_id=row.id)
        try:
            # Cleanup is external I/O; a prior allow cannot authorize the
            # result handoff after this awaited boundary.
            await _recheck_attempt_trust(db, provider_attempt_id=row.id)
        except DelegatedClinicalTrustError as exc:
            await _quarantine_retrieval_denial(
                db,
                provider_attempt_id=row.id,
                job_id=row.job_id,
                error_code=exc.code,
                stager=stager,
            )
            raise
        row = await transition_provider_attempt(
            db,
            provider_attempt_id=row.id,
            expected_version=row.version,
            target_status=ProviderJobStatus.COMPLETE,
            response_complete=True,
            result_retrieval_complete=True,
            observed_page_count=observed,
        )
        await db.commit()
        return result
    except ProviderTimeoutError:
        await db.rollback()
        current = (
            await db.execute(
                select(ExtractionProviderJobRecord)
                .where(ExtractionProviderJobRecord.id == provider_attempt_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            current is not None
            and current.status == ProviderJobStatus.FETCHING_RESULTS.value
        ):
            await transition_provider_attempt(
                db,
                provider_attempt_id=current.id,
                expected_version=current.version,
                target_status=ProviderJobStatus.RECONCILING,
            )
            await db.commit()
        raise
    except Exception:
        await db.rollback()
        raise
