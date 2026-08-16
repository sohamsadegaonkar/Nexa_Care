"""PostgreSQL regressions for exact clinical idempotency and graph binding."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from contextlib import asynccontextmanager, contextmanager, nullcontext
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.adjudication import AdjudicationOutcome, AdjudicationReasonCode
from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    DemoExtractionProvider,
    ExtractionProviderResult,
)
from app.models.ai_models import (
    ExtractedMedicalDocument,
    ProviderAttemptOutcome,
    ProviderAttemptTrace,
    ProviderFieldEvidence,
)
from app.models.field_evidence import NormalizedBoundingBox
from app.models.patient_records import TimelineEvent, Vitals
from app.models.pipeline import (
    AdjudicationCaseRecord,
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.models.provider import AffiliationType, HospitalRegistry
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services.adjudication import (
    AdjudicationError,
    commit_submission,
    create_case,
    submit_case,
)
from app.services.crypto_kms import EncryptedField
from app.services.pipeline_orchestrator import process_extraction_job

pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _provider(tenant: uuid.UUID) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="C1 qualification reviewer",
            contact_email="c1@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=tenant,
            facility_code="C1",
            display_name="C1 qualification facility",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=["clinician"],
            is_primary=True,
        ),
    )


def _field(
    effective_at: datetime,
    *,
    value: float = 72.0,
    vital_type: str = "HEART_RATE",
    test_name: str | None = None,
) -> dict:
    if test_name is not None:
        return {
            "kind": "LAB_RESULT",
            "test_name": test_name,
            "reviewer_entered_value": value,
            "normalized_value": value,
            "unit": "mg/dL",
            "reference_range": "10-20",
            "is_abnormal": False,
            "effective_at": effective_at,
            "page_number": 0,
            "provenance_type": "HUMAN_TRANSCRIBED",
        }
    return {
        "kind": "VITAL",
        "vital_type": vital_type,
        "reviewer_entered_value": value,
        "normalized_value": value,
        "unit": "beats/min",
        "effective_at": effective_at,
        "page_number": 0,
        "provenance_type": "HUMAN_TRANSCRIBED",
    }


@asynccontextmanager
async def _fixture(
    case_count: int = 2,
    *,
    separate_documents: bool = False,
    separate_tenants: bool = False,
    job_status: str = "source_only",
    create_cases: bool = True,
):
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    tenants = (
        [uuid.uuid4() for _ in range(case_count)]
        if separate_tenants
        else [tenant] * case_count
    )
    patients = [uuid.uuid4() for _ in range(case_count)]
    documents = [uuid.uuid4() for _ in range(case_count if separate_documents else 1)]
    jobs = [uuid.uuid4() for _ in range(case_count)]
    provider = _provider(tenant)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        async with factory() as db:
            for hospital_id in sorted(set(tenants)):
                db.add(
                    HospitalRegistry(
                        id=hospital_id,
                        facility_code=f"C1-{uuid.uuid4().hex[:10]}",
                        legal_name="C1 qualification facility",
                        display_name="C1 qualification facility",
                        country_code="IN",
                        is_active=True,
                    )
                )
            await db.flush()
            for patient in patients:
                await db.execute(
                    text("INSERT INTO public.patients (patient_uuid) VALUES (:id)"),
                    {"id": patient},
                )
            for index, document_id in enumerate(documents):
                patient = patients[index] if separate_documents else patients[0]
                db.add(
                    DocumentStorage(
                        id=document_id,
                        patient_id=patient,
                        tenant_id=tenants[index] if separate_documents else tenants[0],
                        uploader_id=provider.actor_uid,
                        storage_ref=f"c1-{document_id}",
                        content_type="application/pdf",
                        size=16,
                        content_hash=uuid.uuid4().hex * 2,
                        upload_purpose="c1-qualification",
                        consent_session_id=f"c1-consent-{document_id}",
                        source_system="C1_SYNTHETIC",
                        uploaded_at=now,
                    )
                )
            await db.flush()
            for index, job_id in enumerate(jobs):
                document_id = documents[index] if separate_documents else documents[0]
                patient = patients[index] if separate_documents else patients[0]
                db.add(
                    ExtractionJob(
                        id=job_id,
                        patient_id=patient,
                        tenant_id=tenants[index] if separate_documents else tenants[0],
                        uploader_id=provider.actor_uid,
                        authorization_provider_id=provider.actor_uid,
                        consent_request_id=f"c1-workflow-{job_id}",
                        document_id=document_id,
                        document_type="qualification",
                        status=job_status,
                        request_id=f"c1-job-{job_id}",
                        attempt_count=1,
                        retryable=False,
                        version=1,
                        created_at=now,
                    )
                )
            await db.commit()

        cases: list[tuple[uuid.UUID, str]] = []
        if create_cases:
            for job_id in jobs:
                session_id = f"c1-session-{uuid.uuid4().hex}"
                async with factory() as db:
                    with patch("app.services.adjudication._live_access", AsyncMock()):
                        case = await create_case(
                            db,
                            provider=provider,
                            idempotency_key=f"c1-case-{uuid.uuid4().hex}",
                            review_session_id=session_id,
                            job_id=job_id,
                        )
                    await db.commit()
                    cases.append((case.id, session_id))
        yield factory, provider, patients, documents, jobs, cases
    finally:
        async with factory() as db:
            params = {
                "patients": [str(value) for value in patients],
                "documents": [str(value) for value in documents],
                "jobs": [str(value) for value in jobs],
                "tenants": [str(value) for value in tenants],
            }
            statements = (
                "DELETE FROM audit_outbox WHERE patient_id = ANY(CAST(:patients AS text[]))",
                "DELETE FROM timeline_events WHERE patient_id = ANY(CAST(:patients AS uuid[]))",
                "DELETE FROM patient_vitals WHERE patient_id = ANY(CAST(:patients AS uuid[]))",
                "DELETE FROM patient_lab_results WHERE patient_id = ANY(CAST(:patients AS uuid[]))",
                "ALTER TABLE extraction_attempt_events DISABLE TRIGGER trg_extraction_attempt_events_immutable",
                "DELETE FROM adjudication_conflict_resolutions WHERE case_id IN (SELECT id FROM adjudication_cases WHERE job_id = ANY(CAST(:jobs AS uuid[])))",
                "UPDATE adjudication_cases SET accepted_submission_id = NULL WHERE job_id = ANY(CAST(:jobs AS uuid[]))",
                "DELETE FROM adjudication_submissions WHERE case_id IN (SELECT id FROM adjudication_cases WHERE job_id = ANY(CAST(:jobs AS uuid[])))",
                "DELETE FROM adjudication_cases WHERE job_id = ANY(CAST(:jobs AS uuid[]))",
                "DELETE FROM extraction_routing WHERE job_id = ANY(CAST(:jobs AS uuid[]))",
                "DELETE FROM extraction_decisions WHERE job_id = ANY(CAST(:jobs AS uuid[]))",
                "DELETE FROM extraction_candidates WHERE job_id = ANY(CAST(:jobs AS uuid[]))",
                "DELETE FROM extraction_attempt_events WHERE job_id = ANY(CAST(:jobs AS uuid[]))",
                "DELETE FROM extraction_jobs WHERE id = ANY(CAST(:jobs AS uuid[]))",
                "ALTER TABLE extraction_attempt_events ENABLE TRIGGER trg_extraction_attempt_events_immutable",
                "DELETE FROM document_storage WHERE id = ANY(CAST(:documents AS uuid[]))",
                "DELETE FROM public.patients WHERE patient_uuid = ANY(CAST(:patients AS uuid[]))",
                "DELETE FROM hospital_registry WHERE id = ANY(CAST(:tenants AS uuid[]))",
            )
            for statement in statements:
                await db.execute(text(statement), params)
            await db.commit()
        await engine.dispose()


async def _submit(
    factory: async_sessionmaker[AsyncSession],
    provider: ProviderContext,
    case_id: uuid.UUID,
    session_id: str,
    fields: list[dict],
) -> uuid.UUID:
    async with factory() as db:
        with patch("app.services.adjudication._live_access", AsyncMock()):
            row = await submit_case(
                db,
                case_id=case_id,
                provider=provider,
                review_session_id=session_id,
                outcome=AdjudicationOutcome.ACCEPTED,
                fields=fields,
                reason_codes=[AdjudicationReasonCode.SOURCE_VERIFIED],
                idempotency_key=f"c1-submission-{uuid.uuid4().hex}",
            )
        await db.commit()
        return row.id


async def _commit(
    factory: async_sessionmaker[AsyncSession],
    provider: ProviderContext,
    submission_id: uuid.UUID,
    session_id: str,
    *,
    patch_live: bool = True,
) -> uuid.UUID | str:
    async with factory() as db:
        try:
            context = (
                patch("app.services.adjudication._live_access", AsyncMock())
                if patch_live
                else nullcontext()
            )
            with context:
                case = await commit_submission(
                    db,
                    submission_id=submission_id,
                    provider=provider,
                    review_session_id=session_id,
                )
            await db.commit()
            return case.id
        except AdjudicationError as exc:
            await db.rollback()
            return exc.code


@pytest.mark.asyncio
async def test_scenario_10_same_source_two_cases_reuse_one_clinical_fact():
    async with _fixture(case_count=2) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        cases,
    ):
        effective_at = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        submissions = [
            await _submit(factory, provider, case_id, session, [_field(effective_at)])
            for case_id, session in cases
        ]
        assert (
            await _commit(factory, provider, submissions[0], cases[0][1]) == cases[0][0]
        )
        assert (
            await _commit(factory, provider, submissions[1], cases[1][1]) == cases[1][0]
        )
        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(Vitals.id)).where(
                        Vitals.patient_id == patients[0],
                        Vitals.source_document_id == documents[0],
                    )
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    select(func.count(TimelineEvent.id)).where(
                        TimelineEvent.patient_id == patients[0],
                        TimelineEvent.source == "human_adjudicated",
                    )
                )
            ).scalar_one() == 1
            committed = (
                await db.execute(
                    select(func.count(AdjudicationCaseRecord.id)).where(
                        AdjudicationCaseRecord.job_id.in_(jobs),
                        AdjudicationCaseRecord.clinical_committed_at.is_not(None),
                    )
                )
            ).scalar_one()
            assert committed == 2


@pytest.mark.asyncio
async def test_scenario_10_concurrent_same_source_cases_are_idempotent():
    async with _fixture(case_count=2) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        cases,
    ):
        effective_at = datetime(2026, 8, 15, 12, 1, tzinfo=timezone.utc)
        submissions = [
            await _submit(factory, provider, case_id, session, [_field(effective_at)])
            for case_id, session in cases
        ]
        with patch("app.services.adjudication._live_access", AsyncMock()):
            results = await asyncio.gather(
                *(
                    _commit(factory, provider, submission, session, patch_live=False)
                    for submission, (_, session) in zip(submissions, cases)
                )
            )
        assert results == [cases[0][0], cases[1][0]]
        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(Vitals.id)).where(
                        Vitals.patient_id == patients[0],
                        Vitals.source_document_id == documents[0],
                        Vitals.recorded_at == effective_at,
                    )
                )
            ).scalar_one() == 1


@pytest.mark.asyncio
async def test_scenario_10_conflicting_same_source_fact_fails_closed():
    async with _fixture(case_count=2) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        cases,
    ):
        effective_at = datetime(2026, 8, 15, 12, 2, tzinfo=timezone.utc)
        first = await _submit(
            factory,
            provider,
            cases[0][0],
            cases[0][1],
            [_field(effective_at, value=70.0)],
        )
        second = await _submit(
            factory,
            provider,
            cases[1][0],
            cases[1][1],
            [_field(effective_at, value=71.0)],
        )
        assert await _commit(factory, provider, first, cases[0][1]) == cases[0][0]
        assert (
            await _commit(factory, provider, second, cases[1][1])
            == "ADJUDICATION_CLINICAL_FACT_COLLISION"
        )
        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(Vitals.id)).where(
                        Vitals.patient_id == patients[0],
                        Vitals.source_document_id == documents[0],
                    )
                )
            ).scalar_one() == 1
            case = (
                await db.execute(
                    select(AdjudicationCaseRecord).where(
                        AdjudicationCaseRecord.id == cases[1][0]
                    )
                )
            ).scalar_one()
            assert case.clinical_committed_at is None


@pytest.mark.asyncio
async def test_scenario_10_distinct_facts_and_sources_remain_distinct():
    async with _fixture(case_count=2, separate_documents=True) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        cases,
    ):
        effective_at = datetime(2026, 8, 15, 12, 3, tzinfo=timezone.utc)
        first = await _submit(
            factory, provider, cases[0][0], cases[0][1], [_field(effective_at)]
        )
        second = await _submit(
            factory, provider, cases[1][0], cases[1][1], [_field(effective_at)]
        )
        assert await _commit(factory, provider, first, cases[0][1]) == cases[0][0]
        assert await _commit(factory, provider, second, cases[1][1]) == cases[1][0]
        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(Vitals.id)).where(
                        Vitals.source == "human_adjudicated",
                        Vitals.patient_id.in_(patients),
                    )
                )
            ).scalar_one() == 2


@pytest.mark.asyncio
async def test_scenario_10_duplicate_identity_inside_submission_fails_atomically():
    async with _fixture(case_count=1) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        cases,
    ):
        effective_at = datetime(2026, 8, 15, 12, 4, tzinfo=timezone.utc)
        submission = await _submit(
            factory,
            provider,
            cases[0][0],
            cases[0][1],
            [_field(effective_at), _field(effective_at)],
        )
        assert (
            await _commit(factory, provider, submission, cases[0][1])
            == "ADJUDICATION_DUPLICATE_CLINICAL_FACT"
        )
        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(Vitals.id)).where(
                        Vitals.patient_id == patients[0]
                    )
                )
            ).scalar_one() == 0


async def _assert_fk_violation(db: AsyncSession, row, constraint: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    original = exc.value.orig
    assert getattr(original, "sqlstate", None) == "23503"
    cause = getattr(original, "__cause__", None)
    assert (
        getattr(getattr(original, "diag", None), "constraint_name", None)
        or getattr(cause, "constraint_name", None)
        or getattr(getattr(cause, "diag", None), "constraint_name", None)
    ) == constraint


async def _assert_check_violation(db: AsyncSession, row, constraint: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    original = exc.value.orig
    assert getattr(original, "sqlstate", None) == "23514"
    cause = getattr(original, "__cause__", None)
    assert (
        getattr(getattr(original, "diag", None), "constraint_name", None)
        or getattr(cause, "constraint_name", None)
        or getattr(getattr(cause, "diag", None), "constraint_name", None)
    ) == constraint


class _PipelineProvider(DemoExtractionProvider):
    def __init__(self, label: str):
        self.label = label
        self.calls = 0

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ):
        assert document_bytes == b"c1-pipeline"
        assert mime_type == "application/pdf"
        assert request_id
        self.calls += 1
        now = datetime.now(timezone.utc)
        block = f"c1-{self.label}-{self.calls}"
        field = ProviderFieldEvidence(
            canonical_field_name="hba1c",
            raw_value="7.2 %",
            source_text="HbA1c: 7.2 %",
            page_number=0,
            bounding_box=NormalizedBoundingBox(
                left=0.1, top=0.2, right=0.4, bottom=0.3
            ),
            field_confidence=0.99,
            provider_name="c1-provider",
            provider_api_version="c1-v1",
            extraction_timestamp=now,
            evidence_hash=hashlib.sha256(block.encode()).hexdigest(),
            source_type="QUERY_RESULT",
            source_block_ids=(block,),
            normalized_value="7.2",
            raw_unit="%",
            normalized_unit="%",
        )
        document = ExtractedMedicalDocument(
            patient_name="",
            phone="",
            aadhaar_abha_id="",
            diagnoses=[],
            lab_results=[],
            prescriptions=[],
            extraction_confidence=0.99,
            field_evidence=[field],
        )
        trace = ProviderAttemptTrace(
            provider_subattempt_number=1,
            provider_adapter="demo",
            provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
            provider_model_version="c1-v1",
            outcome=ProviderAttemptOutcome.SUCCEEDED,
            error_code=None,
            response_complete=True,
            occurred_at=now,
        )
        return ExtractionProviderResult(
            document=document,
            provider_adapter="demo",
            provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
            provider_model_version="c1-v1",
            response_complete=True,
            provider_attempt_traces=(trace,),
        )


class _PipelineKms:
    async def encrypt_field(self, _patient_id, field_name, value, _db):
        return EncryptedField(
            ciphertext=str(value).encode(),
            iv=b"1" * 12,
            field_name=field_name,
            dek_version=1,
            algorithm="AES-256-GCM",
        )


async def _run_pipeline_job(factory, job_id, provider, storage, kms):
    async with factory() as db:
        return await process_extraction_job(str(job_id), db)


@contextmanager
def _pipeline_patches(provider, storage, kms):
    with (
        patch(
            "app.services.pipeline_orchestrator.get_document_storage",
            return_value=storage,
        ),
        patch(
            "app.services.pipeline_orchestrator.get_medical_document_extractor",
            return_value=provider,
        ),
        patch(
            "app.services.pipeline_orchestrator.get_document_extraction_config",
            return_value=type(
                "Config",
                (),
                {
                    "provider": "demo",
                    "provider_max_attempts": 2,
                    "job_max_attempts": 2,
                },
            )(),
        ),
        patch(
            "app.services.pipeline_orchestrator.get_encryption_provider",
            return_value=kms,
        ),
        patch(
            "app.services.pipeline_orchestrator.validate_live_document_processing_request",
            AsyncMock(return_value=object()),
        ),
        patch(
            "app.services.pipeline_orchestrator.check_erasure_registry",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline_orchestrator._assess_extracted_identity",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline_orchestrator.append_audit_log_or_503",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.pipeline_orchestrator.enqueue_audit_event",
            AsyncMock(return_value=None),
        ),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "separate_tenants", [False, True], ids=["same-tenant", "cross-tenant"]
)
async def test_scenario_19_pipeline_graphs_remain_isolated(separate_tenants: bool):
    async with _fixture(
        case_count=2,
        separate_documents=True,
        separate_tenants=separate_tenants,
        job_status="queued",
        create_cases=False,
    ) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        _cases,
    ):
        storage = type(
            "Storage",
            (),
            {"get_document_bytes": AsyncMock(return_value=b"c1-pipeline")},
        )()
        kms = _PipelineKms()
        provider = _PipelineProvider("shared")
        with _pipeline_patches(provider, storage, kms):
            results = await asyncio.gather(
                *(
                    _run_pipeline_job(factory, job_id, provider, storage, kms)
                    for job_id in jobs
                )
            )
        assert all(result["status"] == "source_only" for result in results), results
        async with factory() as db:
            candidates = (
                (
                    await db.execute(
                        select(ExtractionCandidateRecord)
                        .where(ExtractionCandidateRecord.job_id.in_(jobs))
                        .order_by(ExtractionCandidateRecord.job_id)
                    )
                )
                .scalars()
                .all()
            )
            decisions = (
                (
                    await db.execute(
                        select(ExtractionDecisionRecord)
                        .where(ExtractionDecisionRecord.job_id.in_(jobs))
                        .order_by(ExtractionDecisionRecord.job_id)
                    )
                )
                .scalars()
                .all()
            )
            routes = (
                (
                    await db.execute(
                        select(ExtractionRoutingRecord)
                        .where(ExtractionRoutingRecord.job_id.in_(jobs))
                        .order_by(ExtractionRoutingRecord.job_id)
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await db.execute(
                        text(
                            "SELECT job_id, patient_id, tenant_id, source_document_id "
                            "FROM extraction_attempt_events WHERE job_id = ANY(CAST(:jobs AS uuid[]))"
                        ),
                        {"jobs": [str(value) for value in jobs]},
                    )
                )
                .mappings()
                .all()
            )
            assert (
                len(candidates) == len(decisions) == len(routes) == len(events) == 2
            ), results
            for index, job_id in enumerate(sorted(jobs)):
                fixture_index = jobs.index(job_id)
                assert (
                    candidates[index].job_id
                    == decisions[index].job_id
                    == routes[index].job_id
                    == job_id
                )
                assert (
                    candidates[index].patient_id
                    == decisions[index].patient_id
                    == routes[index].patient_id
                    == patients[fixture_index]
                )
                assert (
                    candidates[index].source_document_id
                    == decisions[index].source_document_id
                    == routes[index].source_document_id
                    == documents[fixture_index]
                )
                event = next(item for item in events if item["job_id"] == job_id)
                assert event["patient_id"] == patients[fixture_index]
                assert event["source_document_id"] == documents[fixture_index]


@pytest.mark.asyncio
async def test_scenario_19_same_job_concurrency_and_completed_replay_are_idempotent():
    async with _fixture(
        case_count=1,
        separate_documents=True,
        job_status="queued",
        create_cases=False,
    ) as (factory, _provider_context, _patients, _documents, jobs, _cases):
        storage = type(
            "Storage",
            (),
            {"get_document_bytes": AsyncMock(return_value=b"c1-pipeline")},
        )()
        provider = _PipelineProvider("same-job")
        kms = _PipelineKms()
        async with factory() as db:
            job = await db.get(ExtractionJob, jobs[0])
            job.attempt_count = 0
            await db.commit()
        with _pipeline_patches(provider, storage, kms):
            results = await asyncio.gather(
                *(
                    _run_pipeline_job(factory, jobs[0], provider, storage, kms)
                    for _ in range(2)
                )
            )
            assert {result["status"] for result in results} <= {
                "source_only",
                "extracting",
            }
            assert provider.calls == 1
            async with factory() as db:
                job = await db.get(ExtractionJob, jobs[0])
                counts = [
                    (
                        await db.execute(
                            select(func.count(model.id)).where(model.job_id == jobs[0])
                        )
                    ).scalar_one()
                    for model in (
                        ExtractionCandidateRecord,
                        ExtractionDecisionRecord,
                        ExtractionRoutingRecord,
                    )
                ]
                event_count = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM extraction_attempt_events "
                            "WHERE job_id = :job"
                        ),
                        {"job": jobs[0]},
                    )
                ).scalar_one()
                assert job.status == "source_only"
                assert job.attempt_count == 1
                assert counts == [1, 1, 1]
                assert event_count == 1
            before_counts = (job.attempt_count, *counts, event_count)
            replay = await _run_pipeline_job(factory, jobs[0], provider, storage, kms)
            assert replay == {
                "job_id": str(jobs[0]),
                "status": "source_only",
                "idempotent": True,
            }
            assert provider.calls == 1
            async with factory() as db:
                replay_job = await db.get(ExtractionJob, jobs[0])
                replay_counts = [
                    (
                        await db.execute(
                            select(func.count(model.id)).where(model.job_id == jobs[0])
                        )
                    ).scalar_one()
                    for model in (
                        ExtractionCandidateRecord,
                        ExtractionDecisionRecord,
                        ExtractionRoutingRecord,
                    )
                ]
                replay_events = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM extraction_attempt_events "
                            "WHERE job_id = :job"
                        ),
                        {"job": jobs[0]},
                    )
                ).scalar_one()
                assert (
                    replay_job.attempt_count,
                    *replay_counts,
                    replay_events,
                ) == before_counts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch_field", ["patient_id", "tenant_id", "source_document_id"]
)
async def test_scenario_19_direct_graph_mismatches_fail_with_named_fks(
    mismatch_field: str,
):
    async with _fixture(case_count=2, separate_documents=True) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        cases,
    ):
        async with factory() as db:
            mismatch = {
                "patient_id": patients[1],
                "tenant_id": uuid.uuid4(),
                "source_document_id": documents[1],
            }
            candidate = ExtractionCandidateRecord(
                evidence_id=uuid.uuid4(),
                job_id=jobs[0],
                source_document_id=(
                    mismatch["source_document_id"]
                    if mismatch_field == "source_document_id"
                    else documents[0]
                ),
                patient_id=(
                    mismatch["patient_id"]
                    if mismatch_field == "patient_id"
                    else patients[0]
                ),
                tenant_id=(
                    mismatch["tenant_id"]
                    if mismatch_field == "tenant_id"
                    else provider.hospital.hospital_id
                ),
                authorization_provider_id=provider.actor_uid,
                field_name="lab_result",
                encrypted_raw_value="synthetic-ciphertext",
                provider_name="C1",
                provider_version="1",
                extracted_at=datetime.now(timezone.utc),
                evidence_complete=True,
                lane="SOURCE_ONLY",
                reason_codes=[],
                routing_eligible=True,
                eligibility_policy_version="v1",
                created_at=datetime.now(timezone.utc),
            )
            await _assert_fk_violation(
                db,
                candidate,
                "fk_extraction_candidates_authoritative_job_graph",
            )
            decision = ExtractionDecisionRecord(
                evidence_id=uuid.uuid4(),
                patient_id=(
                    mismatch["patient_id"]
                    if mismatch_field == "patient_id"
                    else patients[0]
                ),
                tenant_id=(
                    mismatch["tenant_id"]
                    if mismatch_field == "tenant_id"
                    else provider.hospital.hospital_id
                ),
                organization_id=(
                    mismatch["tenant_id"]
                    if mismatch_field == "tenant_id"
                    else provider.hospital.hospital_id
                ),
                source_document_id=(
                    mismatch["source_document_id"]
                    if mismatch_field == "source_document_id"
                    else documents[0]
                ),
                job_id=jobs[0],
                decision_contract_version="1.0",
                evidence_contract_version="1.0",
                workflow_id="c1-workflow",
                request_id="c1-request",
                attempt_id="c1-attempt",
                lane="SOURCE_ONLY",
                reason_codes=[],
                policy_version="source-only/1",
                policy_configuration_hash="a" * 64,
                evidence_digest="b" * 64,
                evaluated_at=datetime.now(timezone.utc),
                evaluator_version="c1",
                auto_commit_feature_enabled=False,
                created_at=datetime.now(timezone.utc),
            )
            await _assert_fk_violation(
                db,
                decision,
                "fk_extraction_decisions_authoritative_job_graph",
            )
            valid_decision = ExtractionDecisionRecord(
                evidence_id=uuid.uuid4(),
                patient_id=patients[0],
                tenant_id=provider.hospital.hospital_id,
                organization_id=provider.hospital.hospital_id,
                source_document_id=documents[0],
                job_id=jobs[0],
                decision_contract_version="1.0",
                evidence_contract_version="1.0",
                workflow_id="c1-workflow-valid",
                request_id="c1-request-valid",
                attempt_id="c1-attempt-valid",
                lane="SOURCE_ONLY",
                reason_codes=[],
                policy_version="source-only/1",
                policy_configuration_hash="c" * 64,
                evidence_digest="d" * 64,
                evaluated_at=datetime.now(timezone.utc),
                evaluator_version="c1",
                auto_commit_feature_enabled=False,
                created_at=datetime.now(timezone.utc),
            )
            db.add(valid_decision)
            await db.flush()
            routing = ExtractionRoutingRecord(
                decision_id=valid_decision.id,
                job_id=jobs[0],
                patient_id=(
                    mismatch["patient_id"]
                    if mismatch_field == "patient_id"
                    else patients[0]
                ),
                tenant_id=(
                    mismatch["tenant_id"]
                    if mismatch_field == "tenant_id"
                    else provider.hospital.hospital_id
                ),
                source_document_id=(
                    mismatch["source_document_id"]
                    if mismatch_field == "source_document_id"
                    else documents[0]
                ),
                lane="SOURCE_ONLY",
                status="SOURCE_RETAINED",
                routed_at=datetime.now(timezone.utc),
                idempotency_key=f"c1-route-{uuid.uuid4().hex}",
                operation_hash="e" * 64,
                created_at=datetime.now(timezone.utc),
            )
            await _assert_fk_violation(
                db,
                routing,
                "fk_extraction_routing_authoritative_decision_graph",
            )


@pytest.mark.asyncio
async def test_scenario_19_decision_organization_must_equal_tenant_in_database():
    async with _fixture(
        case_count=2,
        separate_documents=True,
        create_cases=False,
    ) as (
        factory,
        provider,
        patients,
        documents,
        jobs,
        cases,
    ):
        async with factory() as db:
            tenant_a = provider.hospital.hospital_id
            tenant_b = uuid.uuid4()
            assert tenant_a != tenant_b
            db.add(
                HospitalRegistry(
                    id=tenant_b,
                    facility_code=f"C1-{uuid.uuid4().hex[:10]}",
                    legal_name="C1 organization mismatch facility",
                    display_name="C1 organization mismatch facility",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.flush()
            valid = ExtractionDecisionRecord(
                evidence_id=uuid.uuid4(),
                patient_id=patients[0],
                tenant_id=tenant_a,
                organization_id=tenant_a,
                source_document_id=documents[0],
                job_id=jobs[0],
                decision_contract_version="1.0",
                evidence_contract_version="1.0",
                workflow_id="c1-org-valid-workflow",
                request_id="c1-org-valid-request",
                attempt_id="c1-org-valid-attempt",
                lane="SOURCE_ONLY",
                reason_codes=[],
                policy_version="source-only/1",
                policy_configuration_hash="f" * 64,
                evidence_digest="1" * 64,
                evaluated_at=datetime.now(timezone.utc),
                evaluator_version="c1",
                auto_commit_feature_enabled=False,
                created_at=datetime.now(timezone.utc),
            )
            db.add(valid)
            await db.flush()
            mismatch = ExtractionDecisionRecord(
                evidence_id=uuid.uuid4(),
                patient_id=patients[0],
                tenant_id=tenant_a,
                organization_id=tenant_b,
                source_document_id=documents[0],
                job_id=jobs[0],
                decision_contract_version="1.0",
                evidence_contract_version="1.0",
                workflow_id="c1-org-mismatch-workflow",
                request_id="c1-org-mismatch-request",
                attempt_id="c1-org-mismatch-attempt",
                lane="SOURCE_ONLY",
                reason_codes=[],
                policy_version="source-only/1",
                policy_configuration_hash="2" * 64,
                evidence_digest="3" * 64,
                evaluated_at=datetime.now(timezone.utc),
                evaluator_version="c1",
                auto_commit_feature_enabled=False,
                created_at=datetime.now(timezone.utc),
            )
            await _assert_check_violation(
                db, mismatch, "ck_extraction_decisions_organization_tenant"
            )
            assert (
                await db.execute(
                    select(func.count(ExtractionDecisionRecord.id)).where(
                        ExtractionDecisionRecord.job_id == jobs[0]
                    )
                )
            ).scalar_one() == 1
