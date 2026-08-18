"""Disposable PostgreSQL qualification for the A2 provider-job lifecycle."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.pipeline import (
    DocumentStorage,
    ExtractionJob,
    ExtractionProviderJobRecord,
)
from app.models.provider import HospitalRegistry
from app.services.provider_job_lifecycle import (
    ProviderJobLifecycleError,
    ProviderJobStatus,
    ProviderReconciliationOutcome,
    ReconciliationOutcomeType,
    apply_reconciliation_outcome,
    begin_provider_submission,
    claim_due_provider_reconciliations,
    create_provider_attempt,
    mark_local_wait_expired,
)
from app.services.provider_job_reconciliation_processor import (
    run_provider_job_reconciliation_processor_forever,
)


pytestmark = pytest.mark.postgres


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _setup(factory, *, attempt_number: int = 1):
    tenant, patient, document_id, job_id = (uuid.uuid4() for _ in range(4))
    now = datetime.now(timezone.utc)
    async with factory() as db:
        await db.execute(
            text("INSERT INTO public.patients (patient_uuid) VALUES (:patient)"),
            {"patient": patient},
        )
        db.add(
            HospitalRegistry(
                id=tenant,
                facility_code=f"A2-{uuid.uuid4().hex[:20]}",
                legal_name="A2 synthetic facility",
                display_name="A2 synthetic facility",
                country_code="IN",
                is_active=True,
            )
        )
        await db.flush()
        db.add(
            DocumentStorage(
                id=document_id,
                patient_id=patient,
                tenant_id=tenant,
                uploader_id="A2_SYNTHETIC",
                storage_ref=f"a2-{document_id}",
                content_type="application/pdf",
                size=32,
                content_hash=uuid.uuid4().hex * 2,
                uploaded_at=now,
            )
        )
        db.add(
            ExtractionJob(
                id=job_id,
                patient_id=patient,
                tenant_id=tenant,
                uploader_id="A2_SYNTHETIC",
                authorization_provider_id="A2_SYNTHETIC",
                consent_request_id="A2_SYNTHETIC",
                document_id=document_id,
                document_type="application/pdf",
                status="extracting",
                request_id=f"a2-{uuid.uuid4().hex}",
                attempt_count=0,
                retryable=True,
                version=1,
                created_at=now,
            )
        )
        await db.commit()
    return tenant, patient, document_id, job_id


async def _create(factory, ids, *, attempt_number: int = 1, token: str | None = None):
    tenant, patient, document_id, job_id = ids
    async with factory() as db:
        row = await create_provider_attempt(
            db,
            job_id=job_id,
            tenant_id=tenant,
            patient_id=patient,
            source_document_id=document_id,
            job_attempt_number=attempt_number,
            provider_adapter="synthetic",
            provider_contract_version="synthetic/1",
            provider_model_version="synthetic-model",
            client_request_token_digest=token or uuid.uuid4().hex,
            provider_request_fingerprint="f" * 64,
        )
        await db.commit()
        return row.id


@pytest.mark.asyncio
async def test_create_replay_timeout_and_synthetic_success_chain():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _setup(factory)
    try:
        replay_token = f"token-a2-replay-{ids[3].hex}"
        attempt_id = await _create(factory, ids, token=replay_token)
        async with factory() as db:
            replay = await create_provider_attempt(
                db,
                job_id=ids[3],
                tenant_id=ids[0],
                patient_id=ids[1],
                source_document_id=ids[2],
                job_attempt_number=1,
                provider_adapter="synthetic",
                provider_contract_version="synthetic/1",
                provider_model_version="synthetic-model",
                client_request_token_digest=replay_token,
                provider_request_fingerprint="f" * 64,
            )
            assert replay.id == attempt_id
            with pytest.raises(ProviderJobLifecycleError) as collision:
                await create_provider_attempt(
                    db,
                    job_id=ids[3],
                    tenant_id=ids[0],
                    patient_id=ids[1],
                    source_document_id=ids[2],
                    job_attempt_number=1,
                    provider_adapter="synthetic",
                    provider_contract_version="synthetic/1",
                    provider_model_version="different-model",
                    client_request_token_digest=replay_token,
                    provider_request_fingerprint="f" * 64,
                )
            assert collision.value.code == "ASYNC_PROVIDER_ATTEMPT_IDENTITY_COLLISION"
            await db.rollback()

        async with factory() as db:
            row = await begin_provider_submission(
                db, provider_attempt_id=attempt_id, expected_version=1
            )
            assert row.status == ProviderJobStatus.SUBMITTING.value
            assert row.provider_job_id is None
            await db.commit()
        async with factory() as db:
            job_before = await db.get(ExtractionJob, ids[3])
            row = await mark_local_wait_expired(
                db,
                provider_attempt_id=attempt_id,
                expected_version=2,
            )
            assert row.status == ProviderJobStatus.LOCAL_WAIT_EXPIRED.value
            assert row.reconciliation_deadline_at is not None
            await db.commit()
            assert job_before.attempt_count == 0

        async with factory() as db:
            claims = await claim_due_provider_reconciliations(
                db,
                max_attempts=3,
                window_seconds=900,
                interval_seconds=1,
                batch_size=10,
            )
            matching_claims = [
                item for item in claims if item.provider_attempt_id == attempt_id
            ]
            assert len(matching_claims) == 1
            claim = matching_claims[0]
            assert claim.reconciliation_attempt_number == 1
            await db.commit()

        async with factory() as db:
            claimed_row = await db.get(ExtractionProviderJobRecord, attempt_id)
            assert claimed_row.last_reconciled_at is not None
            assert claimed_row.next_reconcile_at is not None
            await db.commit()

        async with factory() as db:
            row = await apply_reconciliation_outcome(
                db,
                claim=claim,
                outcome=ProviderReconciliationOutcome(
                    ReconciliationOutcomeType.SUBMITTED,
                    provider_job_id=f"synthetic-provider-job-{attempt_id.hex}",
                ),
            )
            assert row.status == ProviderJobStatus.SUBMITTED.value
            await db.commit()
        async with factory() as db:
            row = await db.get(ExtractionProviderJobRecord, attempt_id)
            assert row.provider_job_id == f"synthetic-provider-job-{attempt_id.hex}"
            assert row.reconciliation_attempt_count == 1
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_job_id_binds_once_and_rebinding_rolls_back():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _setup(factory)
    try:
        attempt_id = await _create(
            factory, ids, token=f"token-a2-bind-once-{ids[3].hex}"
        )

        async with factory() as db:
            await begin_provider_submission(
                db, provider_attempt_id=attempt_id, expected_version=1
            )
            await mark_local_wait_expired(
                db, provider_attempt_id=attempt_id, expected_version=2
            )
            await db.commit()

        async with factory() as db:
            first_claim = next(
                claim
                for claim in await claim_due_provider_reconciliations(
                    db, interval_seconds=1
                )
                if claim.provider_attempt_id == attempt_id
            )
            await db.commit()

        async with factory() as db:
            row = await apply_reconciliation_outcome(
                db,
                claim=first_claim,
                outcome=ProviderReconciliationOutcome(
                    ReconciliationOutcomeType.SUBMITTED,
                    provider_job_id="JOB_A",
                ),
            )
            assert row.provider_job_id == "JOB_A"
            await db.commit()

        async with factory() as db:
            await mark_local_wait_expired(
                db, provider_attempt_id=attempt_id, expected_version=5
            )
            await db.commit()

        async with factory() as db:
            same_id_claim = next(
                claim
                for claim in await claim_due_provider_reconciliations(
                    db,
                    interval_seconds=1,
                    now=datetime.now(timezone.utc) + timedelta(seconds=10),
                )
                if claim.provider_attempt_id == attempt_id
            )
            await db.commit()

        async with factory() as db:
            row = await apply_reconciliation_outcome(
                db,
                claim=same_id_claim,
                outcome=ProviderReconciliationOutcome(
                    ReconciliationOutcomeType.SUBMITTED,
                    provider_job_id="JOB_A",
                ),
            )
            assert row.provider_job_id == "JOB_A"
            await db.commit()

        async with factory() as db:
            await mark_local_wait_expired(
                db, provider_attempt_id=attempt_id, expected_version=8
            )
            await db.commit()

        async with factory() as db:
            attack_claim = next(
                claim
                for claim in await claim_due_provider_reconciliations(
                    db,
                    interval_seconds=1,
                    now=datetime.now(timezone.utc) + timedelta(seconds=20),
                )
                if claim.provider_attempt_id == attempt_id
            )
            await db.commit()

        async with factory() as db:
            with pytest.raises(ProviderJobLifecycleError) as conflict:
                await apply_reconciliation_outcome(
                    db,
                    claim=attack_claim,
                    outcome=ProviderReconciliationOutcome(
                        ReconciliationOutcomeType.SUBMITTED,
                        provider_job_id="JOB_B",
                    ),
                )
            assert conflict.value.code == "ASYNC_PROVIDER_JOB_ID_CONFLICT"
            assert "JOB_A" not in str(conflict.value)
            assert "JOB_B" not in str(conflict.value)
            await db.rollback()

        async with factory() as db:
            row = await db.get(ExtractionProviderJobRecord, attempt_id)
            assert row.provider_job_id == "JOB_A"
            assert row.status == ProviderJobStatus.RECONCILING.value
            assert row.version == attack_claim.claimed_version
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_claim_and_budget_escalation_are_fail_closed():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _setup(factory)
    try:
        attempt_id = await _create(factory, ids, token=f"token-a2-stale-{ids[3].hex}")
        async with factory() as db:
            await begin_provider_submission(
                db, provider_attempt_id=attempt_id, expected_version=1
            )
            await db.commit()
        async with factory() as db:
            await mark_local_wait_expired(
                db, provider_attempt_id=attempt_id, expected_version=2
            )
            await db.commit()
        async with factory() as db:
            claims = await claim_due_provider_reconciliations(db, interval_seconds=1)
            claim = next(
                item for item in claims if item.provider_attempt_id == attempt_id
            )
            await db.commit()
        async with factory() as db:
            await apply_reconciliation_outcome(
                db,
                claim=claim,
                outcome=ProviderReconciliationOutcome(
                    ReconciliationOutcomeType.SUBMITTED,
                    provider_job_id=f"stale-provider-job-{attempt_id.hex}",
                ),
            )
            await db.commit()
        async with factory() as db:
            with pytest.raises(ProviderJobLifecycleError) as stale:
                await apply_reconciliation_outcome(
                    db,
                    claim=claim,
                    outcome=ProviderReconciliationOutcome(
                        ReconciliationOutcomeType.SUCCEEDED
                    ),
                )
            assert stale.value.code == "ASYNC_PROVIDER_STALE_RECONCILIATION_CLAIM"
            await db.rollback()

        budget_ids = await _setup(factory, attempt_number=2)
        budget_attempt = await _create(
            factory,
            budget_ids,
            attempt_number=2,
            token=f"token-a2-budget-{budget_ids[3].hex}",
        )
        async with factory() as db:
            await db.execute(
                text(
                    "UPDATE public.extraction_provider_jobs "
                    "SET status='LOCAL_WAIT_EXPIRED', reconciliation_attempt_count=3, "
                    "next_reconcile_at=now() WHERE id=:id"
                ),
                {"id": budget_attempt},
            )
            await db.commit()
        async with factory() as db:
            claims = await claim_due_provider_reconciliations(db, max_attempts=3)
            assert claims == []
            row = await db.get(ExtractionProviderJobRecord, budget_attempt)
            assert (
                row.status == ProviderJobStatus.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expected_version_race_allows_one_transition_only():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _setup(factory)
    try:
        attempt_id = await _create(
            factory, ids, token=f"token-a2-version-race-{ids[3].hex}"
        )

        async def worker():
            async with factory() as db:
                try:
                    await begin_provider_submission(
                        db, provider_attempt_id=attempt_id, expected_version=1
                    )
                    await db.commit()
                    return "SUCCESS"
                except ProviderJobLifecycleError as failure:
                    await db.rollback()
                    return failure.code

        results = await asyncio.gather(worker(), worker())
        assert sorted(results) == ["ASYNC_PROVIDER_VERSION_CONFLICT", "SUCCESS"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_claims_partition_due_rows_with_skip_locked():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _setup(factory)
    try:
        first = await _create(factory, ids, token=f"token-a2-claim-1-{ids[3].hex}")
        second = await _create(
            factory,
            ids,
            attempt_number=2,
            token=f"token-a2-claim-2-{ids[3].hex}",
        )
        async with factory() as db:
            await begin_provider_submission(
                db, provider_attempt_id=first, expected_version=1
            )
            await begin_provider_submission(
                db, provider_attempt_id=second, expected_version=1
            )
            await mark_local_wait_expired(
                db, provider_attempt_id=first, expected_version=2
            )
            await mark_local_wait_expired(
                db, provider_attempt_id=second, expected_version=2
            )
            await db.commit()

        async def worker():
            async with factory() as db:
                claims = await claim_due_provider_reconciliations(
                    db, batch_size=1, interval_seconds=1
                )
                await db.commit()
                return {claim.provider_attempt_id for claim in claims}

        claimed_a, claimed_b = await asyncio.gather(worker(), worker())
        assert claimed_a.isdisjoint(claimed_b)
        assert claimed_a | claimed_b == {first, second}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_processor_callback_is_outside_claim_transaction_and_shutdown_is_clean():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _setup(factory)
    shutdown = asyncio.Event()
    seen_claims = []
    try:
        attempt_id = await _create(
            factory, ids, token=f"token-a2-processor-{ids[3].hex}"
        )
        async with factory() as db:
            await begin_provider_submission(
                db, provider_attempt_id=attempt_id, expected_version=1
            )
            await mark_local_wait_expired(
                db, provider_attempt_id=attempt_id, expected_version=2
            )
            await db.commit()

        async def callback(claim):
            seen_claims.append(claim)
            shutdown.set()
            return ProviderReconciliationOutcome(ReconciliationOutcomeType.UNREACHABLE)

        await run_provider_job_reconciliation_processor_forever(
            factory,
            reconcile_callback=callback,
            max_attempts=1,
            poll_interval_seconds=0.01,
            shutdown_event=shutdown,
        )
        assert len(seen_claims) == 1
        async with factory() as db:
            row = await db.get(ExtractionProviderJobRecord, attempt_id)
            assert (
                row.status == ProviderJobStatus.PROVIDER_UNREACHABLE_MANUAL_REVIEW.value
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_provider_state(monkeypatch):
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _setup(factory)
    try:
        attempt_id = await _create(factory, ids, token=f"token-a2-audit-{ids[3].hex}")

        async def fail_audit(*args, **kwargs):
            raise RuntimeError("audit backend unavailable")

        monkeypatch.setattr(
            "app.services.provider_job_lifecycle.enqueue_audit_event", fail_audit
        )
        async with factory() as db:
            with pytest.raises(ProviderJobLifecycleError) as failure:
                await begin_provider_submission(
                    db, provider_attempt_id=attempt_id, expected_version=1
                )
            assert failure.value.code == "ASYNC_PROVIDER_AUDIT_REQUIRED"

        async with factory() as db:
            row = await db.get(ExtractionProviderJobRecord, attempt_id)
            assert row.status == ProviderJobStatus.CREATED.value
            assert row.version == 1
            await db.commit()
    finally:
        await engine.dispose()
