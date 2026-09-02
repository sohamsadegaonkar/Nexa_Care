"""Real PostgreSQL qualification for adversarial scenarios 9 and 23."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import Headers
from starlette.requests import Request

from app.api.v2.pipeline_routes import upload_pipeline_document
from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    AwsTextractExtractionProvider,
    DemoExtractionProvider,
    ExtractionProviderResult,
    ProviderTimeoutError,
)
from app.core.config import DocumentExtractionConfig
from app.models.ai_models import (
    ExtractedMedicalDocument,
    ProviderAttemptOutcome,
    ProviderAttemptTrace,
    ProviderFieldEvidence,
)
from app.models.adjudication import AdjudicationOutcome, AdjudicationReasonCode
from app.models.erasure_tombstone import (
    ErasureAssurance,
    ErasureStatus,
    PatientErasureTombstone,
    WrappingKeyType,
)
from app.models.pipeline import (
    AdjudicationCaseRecord,
    AdjudicationConflictResolutionRecord,
    DocumentSourceRelationshipRecord,
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionConflictMemberRecord,
    ExtractionConflictRecord,
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.models.field_evidence import EvidenceIssue, NormalizedBoundingBox
from app.models.provider import HospitalRegistry
from app.services.adjudication import AdjudicationError, commit_submission, submit_case
from app.services import pipeline_orchestrator
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.erasure_registry import ErasureRegistryUnavailable
from app.services.crypto_kms import EncryptedField
from app.services.document_storage import StoredDocument
from app.services.clinical_evidence_integrity import (
    ClinicalEvidenceIntegrityError,
    SourceRelationType,
    clinical_fact_key,
    create_source_relationship,
    persist_conflict_set,
)
from tests.integration.test_adjudication_runtime_postgres import (
    _case,
    _payload,
    _provider,
)

pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _document(
    *,
    tenant: uuid.UUID,
    patient: uuid.UUID,
    document_id: uuid.UUID,
    uploader_id: str = "qualification-reviewer",
) -> DocumentStorage:
    return DocumentStorage(
        id=document_id,
        patient_id=patient,
        tenant_id=tenant,
        uploader_id=uploader_id,
        storage_ref=f"qualification-{document_id}",
        content_type="application/pdf",
        size=16,
        content_hash=uuid.uuid4().hex * 2,
        original_filename=None,
        upload_purpose="qualification",
        consent_session_id="qualification-consent",
        source_system="SYNTHETIC_POLICY_FIXTURE",
        uploaded_at=datetime.now(timezone.utc),
    )


def _candidate(
    *,
    tenant: uuid.UUID,
    patient: uuid.UUID,
    document: uuid.UUID,
    job: uuid.UUID,
    fact_key: str,
) -> ExtractionCandidateRecord:
    evidence_id = uuid.uuid4()
    return ExtractionCandidateRecord(
        id=uuid.uuid4(),
        evidence_id=evidence_id,
        job_id=job,
        source_document_id=document,
        patient_id=patient,
        tenant_id=tenant,
        authorization_provider_id="qualification-reviewer",
        field_name="lab_result",
        clinical_fact_key=fact_key,
        encrypted_raw_value=f"synthetic-ciphertext-{evidence_id}",
        encrypted_source_text=None,
        source_page=0,
        source_bbox=None,
        field_confidence=0.99,
        document_confidence=0.99,
        provider_name="SYNTHETIC_POLICY_FIXTURE",
        provider_version="qualification/1.0",
        extracted_at=datetime.now(timezone.utc),
        evidence_complete=True,
        lane="SOURCE_ONLY",
        reason_codes=["CLINICAL_VALUE_AMBIGUOUS", "AUTO_COMMIT_DISABLED"],
        routing_eligible=True,
        eligibility_reason_code=None,
        eligibility_policy_version="v1",
        created_at=datetime.now(timezone.utc),
    )


def _job(
    *,
    tenant: uuid.UUID,
    patient: uuid.UUID,
    document: uuid.UUID,
    status: str = "source_only",
    consent_request_id: str = "qualification-workflow",
    provider_id: str = "qualification-reviewer",
) -> ExtractionJob:
    return ExtractionJob(
        id=uuid.uuid4(),
        patient_id=patient,
        tenant_id=tenant,
        uploader_id=provider_id,
        authorization_provider_id=provider_id,
        consent_request_id=consent_request_id,
        document_id=document,
        document_type="application/pdf",
        status=status,
        request_id=f"qualification-{uuid.uuid4()}",
        attempt_count=1,
        retryable=False,
        version=1,
        created_at=datetime.now(timezone.utc),
    )


def _live_capability():
    return SimpleNamespace(
        allowed_operations={DocumentProcessingOperation.UPLOAD_DOCUMENT.value}
    )


async def _seed_identity_graph(db, *, tenant: uuid.UUID, patient: uuid.UUID) -> None:
    db.add(
        HospitalRegistry(
            id=tenant,
            facility_code=f"A1-{uuid.uuid4().hex[:12]}",
            legal_name="A1 qualification facility",
            display_name="A1 qualification facility",
            country_code="IN",
            is_active=True,
        )
    )
    await db.flush()
    await db.execute(
        text("INSERT INTO public.patients (patient_uuid) VALUES (:patient)"),
        {"patient": patient},
    )


@pytest.mark.asyncio
async def test_scenario_9_conflict_members_are_durable_idempotent_and_bound() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, document_id, job_id = (uuid.uuid4() for _ in range(4))
    key = clinical_fact_key("lab_result", "synthetic-same-fact")
    assert key is not None
    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add(_document(tenant=tenant, patient=patient, document_id=document_id))
            db.add(
                ExtractionJob(
                    id=job_id,
                    patient_id=patient,
                    tenant_id=tenant,
                    uploader_id="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    consent_request_id="qualification-consent",
                    document_id=document_id,
                    document_type="application/pdf",
                    status="source_only",
                    request_id=f"qualification-{uuid.uuid4()}",
                    attempt_count=1,
                    retryable=False,
                    version=1,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            first = _candidate(
                tenant=tenant,
                patient=patient,
                document=document_id,
                job=job_id,
                fact_key=key,
            )
            second = _candidate(
                tenant=tenant,
                patient=patient,
                document=document_id,
                job=job_id,
                fact_key=key,
            )
            db.add_all([first, second])
            await db.flush()
            conflict = await persist_conflict_set(
                db,
                tenant_id=tenant,
                patient_id=patient,
                job_id=job_id,
                source_document_id=document_id,
                field_name="lab_result",
                fact_key=key,
                candidates=[first, second],
                created_at=datetime.now(timezone.utc),
            )
            same = await persist_conflict_set(
                db,
                tenant_id=tenant,
                patient_id=patient,
                job_id=job_id,
                source_document_id=document_id,
                field_name="lab_result",
                fact_key=key,
                candidates=[first, second],
                created_at=datetime.now(timezone.utc),
            )
            assert same.id == conflict.id
            await db.commit()

        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(ExtractionCandidateRecord.id)).where(
                        ExtractionCandidateRecord.job_id == job_id
                    )
                )
            ).scalar_one() == 2
            assert (
                await db.execute(
                    select(func.count(ExtractionConflictMemberRecord.id)).where(
                        ExtractionConflictMemberRecord.conflict_id == conflict.id
                    )
                )
            ).scalar_one() == 2
            wrong = _candidate(
                tenant=uuid.uuid4(),
                patient=patient,
                document=document_id,
                job=job_id,
                fact_key=key,
            )
            with pytest.raises(
                ClinicalEvidenceIntegrityError, match="BINDING_MISMATCH"
            ):
                await persist_conflict_set(
                    db,
                    tenant_id=tenant,
                    patient_id=patient,
                    job_id=job_id,
                    source_document_id=document_id,
                    field_name="lab_result",
                    fact_key=key,
                    candidates=[first, wrong],
                    created_at=datetime.now(timezone.utc),
                )
            await db.rollback()
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_9_production_orchestrator_persists_exact_conflict() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, document_id = (uuid.uuid4() for _ in range(3))
    job = _job(
        tenant=tenant,
        patient=patient,
        document=document_id,
        status="queued",
    )
    job.attempt_count = 0

    def observation(value: str, block_id: str) -> ProviderFieldEvidence:
        row = ProviderFieldEvidence(
            canonical_field_name="lab_result",
            raw_value=value,
            source_text=f"HbA1c {value} %",
            page_number=0,
            bounding_box=NormalizedBoundingBox(
                left=0.1,
                top=0.1,
                right=0.5,
                bottom=0.2,
            ),
            field_confidence=0.99,
            provider_name="SYNTHETIC_POLICY_FIXTURE",
            provider_api_version="Y",
            extraction_timestamp=datetime.now(timezone.utc),
            evidence_hash=(block_id.encode().hex() + "0" * 64)[:64],
            source_type="CELL",
            source_block_ids=(block_id,),
            normalized_value=value,
            raw_unit="%",
            normalized_unit="%",
            structured_value={
                "test": "HbA1c",
                "result": value,
                "unit": "%",
                "date": "2026-08-14",
            },
        )
        row._bind_trusted_clinical_fact_id("server-exact-hba1c-2026-08-14")
        return row

    extracted = ExtractedMedicalDocument(
        patient_name="",
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
        extraction_confidence=0.99,
        field_evidence=[observation("7.2", "fact-a"), observation("8.4", "fact-b")],
    )
    extracted_result = ExtractionProviderResult(
        document=extracted,
        provider_adapter="demo",
        provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
        provider_model_version="Y",
        response_complete=True,
        provider_attempt_traces=(
            ProviderAttemptTrace(
                provider_subattempt_number=1,
                provider_adapter="demo",
                provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                provider_model_version="Y",
                outcome=ProviderAttemptOutcome.SUCCEEDED,
                error_code=None,
                response_complete=True,
                occurred_at=datetime.now(timezone.utc),
            ),
        ),
    )

    async def encrypt(_patient_id, field_name, value, _db):
        return EncryptedField(
            ciphertext=f"synthetic:{value}".encode(),
            iv=b"1" * 12,
            field_name=field_name,
            dek_version=1,
            algorithm="AES-256-GCM",
        )

    storage = SimpleNamespace(get_document_bytes=AsyncMock(return_value=b"synthetic"))
    # The first controlled fault represents partial synthetic value A that is
    # never returned across the trusted boundary.  Only the complete Y result
    # below is permitted to become durable clinical evidence.
    extractor = DemoExtractionProvider()
    extractor.extract_bytes = AsyncMock(
        side_effect=[
            ProviderTimeoutError(
                "controlled provider timeout",
                provider_attempt_traces=(
                    ProviderAttemptTrace(
                        provider_subattempt_number=1,
                        provider_adapter="demo",
                        provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
                        provider_model_version="X",
                        outcome=ProviderAttemptOutcome.TIMEOUT,
                        error_code="EXTRACTION_PROVIDER_TIMEOUT",
                        response_complete=False,
                        occurred_at=datetime.now(timezone.utc),
                    ),
                ),
            ),
            extracted_result,
        ]
    )
    kms = SimpleNamespace(encrypt_field=AsyncMock(side_effect=encrypt))
    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add(_document(tenant=tenant, patient=patient, document_id=document_id))
            db.add(job)
            await db.commit()

        async with factory() as db:
            # Scenario 9 qualifies conflict persistence; delegated authority is
            # covered by the dedicated real-provenance PostgreSQL suite.
            with (
                patch(
                    "app.services.pipeline_orchestrator."
                    "recheck_delegated_document_processing_trust",
                    AsyncMock(return_value=None),
                ),
                patch(
                    "app.services.pipeline_orchestrator.get_document_storage",
                    return_value=storage,
                ),
                patch(
                    "app.services.pipeline_orchestrator."
                    "get_medical_document_extractor",
                    return_value=extractor,
                ),
                patch(
                    "app.services.pipeline_orchestrator."
                    "get_document_extraction_config",
                    return_value=SimpleNamespace(
                        provider="demo",
                        provider_max_attempts=2,
                        job_max_attempts=2,
                    ),
                ),
                patch(
                    "app.services.pipeline_orchestrator.get_encryption_provider",
                    return_value=kms,
                ),
                patch(
                    "app.services.pipeline_orchestrator."
                    "validate_live_document_processing_request",
                    AsyncMock(return_value=_live_capability()),
                ),
                patch(
                    "app.services.pipeline_orchestrator.append_audit_log_or_503",
                    AsyncMock(return_value=True),
                ),
            ):
                first_result = await pipeline_orchestrator.process_extraction_job(
                    str(job.id), db
                )
                assert first_result == {
                    "job_id": str(job.id),
                    "status": "extraction_failed_retryable",
                    "error_code": "EXTRACTION_PROVIDER_TIMEOUT",
                    "retryable": True,
                }
                assert (
                    await db.execute(
                        select(func.count(ExtractionCandidateRecord.id)).where(
                            ExtractionCandidateRecord.job_id == job.id
                        )
                    )
                ).scalar_one() == 0
                assert (
                    await db.execute(
                        select(func.count(ExtractionDecisionRecord.id)).where(
                            ExtractionDecisionRecord.job_id == job.id
                        )
                    )
                ).scalar_one() == 0
                assert (
                    await db.execute(
                        select(func.count(ExtractionRoutingRecord.id)).where(
                            ExtractionRoutingRecord.job_id == job.id
                        )
                    )
                ).scalar_one() == 0
                assert (
                    await db.execute(
                        select(func.count(ExtractionConflictRecord.id)).where(
                            ExtractionConflictRecord.job_id == job.id
                        )
                    )
                ).scalar_one() == 0
                result = await pipeline_orchestrator.process_extraction_job(
                    str(job.id), db
                )
            assert result["status"] == "source_only"

        async with factory() as db:
            candidates = (
                (
                    await db.execute(
                        select(ExtractionCandidateRecord).where(
                            ExtractionCandidateRecord.job_id == job.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(candidates) == 2
            assert {row.provider_version for row in candidates} == {"Y"}
            assert len({row.clinical_fact_key for row in candidates}) == 1
            assert all(
                "CLINICAL_VALUE_AMBIGUOUS" in row.reason_codes for row in candidates
            )
            conflict = (
                await db.execute(
                    select(ExtractionConflictRecord).where(
                        ExtractionConflictRecord.job_id == job.id
                    )
                )
            ).scalar_one()
            members = (
                (
                    await db.execute(
                        select(ExtractionConflictMemberRecord).where(
                            ExtractionConflictMemberRecord.conflict_id == conflict.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {row.evidence_id for row in members} == {
                row.evidence_id for row in candidates
            }
            decisions = (
                (
                    await db.execute(
                        select(ExtractionDecisionRecord).where(
                            ExtractionDecisionRecord.job_id == job.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(decisions) == 2
            assert all(not row.auto_commit_feature_enabled for row in decisions)
            lifecycle_rows = (
                await db.execute(
                    text(
                        "SELECT job_attempt_number, provider_model_version, outcome, "
                        "response_complete FROM public.extraction_attempt_events "
                        "WHERE job_id = :job ORDER BY job_attempt_number"
                    ),
                    {"job": job.id},
                )
            ).all()
            assert lifecycle_rows == [
                (1, "X", "TIMEOUT", False),
                (2, "Y", "SUCCEEDED", True),
            ]
            persisted_job = await db.get(ExtractionJob, job.id)
            assert persisted_job.extractor_version == "Y"
            audit_payloads = (
                (
                    await db.execute(
                        text(
                            "SELECT payload::text FROM public.audit_outbox "
                            "WHERE patient_id = :patient"
                        ),
                        {"patient": str(patient)},
                    )
                )
                .scalars()
                .all()
            )
            assert audit_payloads
            assert all("7.2" not in row and "8.4" not in row for row in audit_payloads)
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_11_local_invalid_document_creates_no_clinical_graph() -> None:
    """An empty local document is terminal before any fake provider call."""

    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, document_id = (uuid.uuid4() for _ in range(3))
    job = _job(tenant=tenant, patient=patient, document=document_id, status="queued")
    client = Mock()
    provider = AwsTextractExtractionProvider(
        DocumentExtractionConfig(
            provider="aws_textract",
            environment="test",
            provider_max_attempts=2,
            job_max_attempts=2,
        ),
        client,
    )
    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add(_document(tenant=tenant, patient=patient, document_id=document_id))
            db.add(job)
            await db.commit()

        async with factory() as db:
            # Scenario 11 qualifies invalid-document terminal behavior only.
            with (
                patch(
                    "app.services.pipeline_orchestrator."
                    "recheck_delegated_document_processing_trust",
                    AsyncMock(return_value=None),
                ),
                patch(
                    "app.services.pipeline_orchestrator.get_document_storage",
                    return_value=SimpleNamespace(
                        get_document_bytes=AsyncMock(return_value=b"")
                    ),
                ),
                patch(
                    "app.services.pipeline_orchestrator.get_medical_document_extractor",
                    return_value=provider,
                ),
                patch(
                    "app.services.pipeline_orchestrator.get_document_extraction_config",
                    return_value=provider.config,
                ),
                patch(
                    "app.services.pipeline_orchestrator.append_audit_log_or_503",
                    AsyncMock(return_value=True),
                ),
            ):
                result = await pipeline_orchestrator.process_extraction_job(
                    str(job.id), db
                )
            assert result["status"] == "extraction_failed_terminal"
            assert result["error_code"] == "INVALID_DOCUMENT"
            assert result["retryable"] is False
            assert client.analyze_document.call_count == 0

        async with factory() as db:
            for model in (
                ExtractionCandidateRecord,
                ExtractionDecisionRecord,
                ExtractionRoutingRecord,
                ExtractionConflictRecord,
            ):
                assert (
                    await db.execute(
                        select(func.count(model.id)).where(model.job_id == job.id)
                    )
                ).scalar_one() == 0
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM public.extraction_attempt_events "
                        "WHERE job_id = :job"
                    ),
                    {"job": job.id},
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_graph_concurrent_inverse_and_three_node_cycles_fail_closed() -> (
    None
):
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient = uuid.uuid4(), uuid.uuid4()
    first_id, second_id, third_id = (uuid.uuid4() for _ in range(3))

    async def create_edge(source_id: uuid.UUID, related_id: uuid.UUID):
        async with factory() as db:
            try:
                row = await create_source_relationship(
                    db,
                    source_document_id=source_id,
                    related_document_id=related_id,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
                await db.commit()
                return ("PASS", row.id)
            except ClinicalEvidenceIntegrityError as exc:
                await db.rollback()
                return (str(exc), None)

    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            for document_id in (first_id, second_id, third_id):
                db.add(
                    _document(tenant=tenant, patient=patient, document_id=document_id)
                )
                db.add(_job(tenant=tenant, patient=patient, document=document_id))
            await db.commit()

        with patch(
            "app.services.clinical_evidence_integrity."
            "validate_live_document_processing_request",
            AsyncMock(return_value=_live_capability()),
        ):
            inverse = await asyncio.gather(
                create_edge(first_id, second_id),
                create_edge(second_id, first_id),
            )
            assert sum(result[0] == "PASS" for result in inverse) == 1
            assert any(result[0] == "SOURCE_RELATION_CYCLE" for result in inverse)

        async with factory() as db:
            await db.execute(
                text("DELETE FROM public.audit_outbox " "WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_graph_concurrent_three_edge_cycle_is_acyclic() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient = uuid.uuid4(), uuid.uuid4()
    first_id, second_id, third_id = (uuid.uuid4() for _ in range(3))

    async def create_edge(source_id: uuid.UUID, related_id: uuid.UUID):
        async with factory() as db:
            try:
                row = await create_source_relationship(
                    db,
                    source_document_id=source_id,
                    related_document_id=related_id,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.ADDENDUM_TO,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
                await db.commit()
                return ("PASS", row.id)
            except ClinicalEvidenceIntegrityError as exc:
                await db.rollback()
                return (str(exc), None)

    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            for document_id in (first_id, second_id, third_id):
                db.add(
                    _document(tenant=tenant, patient=patient, document_id=document_id)
                )
                db.add(_job(tenant=tenant, patient=patient, document=document_id))
            await db.commit()

        with patch(
            "app.services.clinical_evidence_integrity."
            "validate_live_document_processing_request",
            AsyncMock(return_value=_live_capability()),
        ):
            results = await asyncio.gather(
                create_edge(first_id, second_id),
                create_edge(second_id, third_id),
                create_edge(third_id, first_id),
            )
            assert sum(result[0] == "PASS" for result in results) == 2
            assert any(result[0] == "SOURCE_RELATION_CYCLE" for result in results)

        async with factory() as db:
            rows = (
                (
                    await db.execute(
                        select(DocumentSourceRelationshipRecord).where(
                            DocumentSourceRelationshipRecord.tenant_id == tenant,
                            DocumentSourceRelationshipRecord.patient_id == patient,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 2
            by_source = {
                row.source_document_id: row.related_document_id for row in rows
            }
            for start in by_source:
                cursor = start
                visited = set()
                while cursor in by_source:
                    assert cursor not in visited
                    visited.add(cursor)
                    cursor = by_source[cursor]
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_graph_locks_do_not_serialize_unrelated_patients() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    patient_a, patient_b = uuid.uuid4(), uuid.uuid4()
    a_old, a_new, b_old, b_new = (uuid.uuid4() for _ in range(4))

    async def create_for_b():
        async with factory() as db:
            row = await create_source_relationship(
                db,
                source_document_id=b_new,
                related_document_id=b_old,
                tenant_id=tenant,
                patient_id=patient_b,
                relation_type=SourceRelationType.SUPERSEDES,
                workflow_id="qualification-workflow",
                created_by="qualification-reviewer",
                authorization_provider_id="qualification-reviewer",
                authorization_hospital_id=tenant,
                consent_request_id="qualification-workflow",
                created_at=datetime.now(timezone.utc),
            )
            await db.commit()
            return row.id

    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient_a)
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:patient)"),
                {"patient": patient_b},
            )
            for patient, document_id in (
                (patient_a, a_old),
                (patient_a, a_new),
                (patient_b, b_old),
                (patient_b, b_new),
            ):
                db.add(
                    _document(tenant=tenant, patient=patient, document_id=document_id)
                )
                db.add(_job(tenant=tenant, patient=patient, document=document_id))
            await db.commit()

        with patch(
            "app.services.clinical_evidence_integrity."
            "validate_live_document_processing_request",
            AsyncMock(return_value=_live_capability()),
        ):
            async with factory() as held_db:
                await create_source_relationship(
                    held_db,
                    source_document_id=a_new,
                    related_document_id=a_old,
                    tenant_id=tenant,
                    patient_id=patient_a,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
                b_relation_id = await asyncio.wait_for(create_for_b(), timeout=2.0)
                assert b_relation_id is not None
                await held_db.commit()

        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(DocumentSourceRelationshipRecord.id)).where(
                        DocumentSourceRelationshipRecord.tenant_id == tenant,
                        DocumentSourceRelationshipRecord.patient_id.in_(
                            [patient_a, patient_b]
                        ),
                    )
                )
            ).scalar_one() == 2
            await db.execute(
                text(
                    "DELETE FROM public.audit_outbox "
                    "WHERE patient_id IN (:patient_a, :patient_b)"
                ),
                {"patient_a": str(patient_a), "patient_b": str(patient_b)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_relationship_revalidates_consent_erasure_and_provider_ownership() -> (
    None
):
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def seed_graph(*, related_uploader: str = "qualification-reviewer"):
        tenant, patient = uuid.uuid4(), uuid.uuid4()
        old_document, new_document = uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            old_row = _document(
                tenant=tenant, patient=patient, document_id=old_document
            )
            old_row.uploader_id = related_uploader
            db.add_all(
                [
                    old_row,
                    _document(tenant=tenant, patient=patient, document_id=new_document),
                    _job(tenant=tenant, patient=patient, document=old_document),
                    _job(tenant=tenant, patient=patient, document=new_document),
                ]
            )
            await db.commit()
        return tenant, patient, old_document, new_document

    async def attempt(
        tenant: uuid.UUID,
        patient: uuid.UUID,
        old_document: uuid.UUID,
        new_document: uuid.UUID,
    ):
        async with factory() as db:
            try:
                row = await create_source_relationship(
                    db,
                    source_document_id=new_document,
                    related_document_id=old_document,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
                await db.commit()
                return row
            except ClinicalEvidenceIntegrityError:
                await db.rollback()
                raise

    try:
        clear = await seed_graph()
        with patch(
            "app.services.clinical_evidence_integrity."
            "validate_live_document_processing_request",
            AsyncMock(return_value=_live_capability()),
        ):
            created = await attempt(*clear)
        assert created.id is not None

        wrong_provider = await seed_graph(related_uploader="other-provider")
        with (
            patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=_live_capability()),
            ),
            pytest.raises(
                ClinicalEvidenceIntegrityError,
                match="SOURCE_RELATION_PROVIDER_MISMATCH",
            ),
        ):
            await attempt(*wrong_provider)

        revoked = await seed_graph()
        with (
            patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=None),
            ),
            pytest.raises(
                ClinicalEvidenceIntegrityError, match="SOURCE_RELATION_CONSENT_INACTIVE"
            ),
        ):
            await attempt(*revoked)

        erased = await seed_graph()
        async with factory() as db:
            db.add(
                PatientErasureTombstone(
                    id=uuid.uuid4(),
                    tenant_id=str(erased[0]),
                    patient_ref=str(erased[1]),
                    status=ErasureStatus.ACCESS_BLOCKED.value,
                    assurance_level=ErasureAssurance.ACTIVE_ACCESS_BLOCKED.value,
                    wrapping_key_type=WrappingKeyType.PATIENT.value,
                )
            )
            await db.commit()
        with (
            patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=_live_capability()),
            ),
            pytest.raises(
                ClinicalEvidenceIntegrityError,
                match="SOURCE_RELATION_ERASURE_ACCESS_BLOCKED",
            ),
        ):
            await attempt(*erased)

        unavailable = await seed_graph()
        with (
            patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=_live_capability()),
            ),
            patch(
                "app.services.clinical_evidence_integrity.check_erasure_registry",
                AsyncMock(side_effect=ErasureRegistryUnavailable("synthetic")),
            ),
            pytest.raises(
                ClinicalEvidenceIntegrityError,
                match="SOURCE_RELATION_ERASURE_REGISTRY_UNAVAILABLE",
            ),
        ):
            await attempt(*unavailable)

        async with factory() as db:
            for tenant, patient, _old, _new in (
                wrong_provider,
                revoked,
                erased,
                unavailable,
            ):
                assert (
                    await db.execute(
                        select(func.count(DocumentSourceRelationshipRecord.id)).where(
                            DocumentSourceRelationshipRecord.tenant_id == tenant,
                            DocumentSourceRelationshipRecord.patient_id == patient,
                        )
                    )
                ).scalar_one() == 0
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(clear[1])},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_predecessor_requires_supported_job_and_exact_durable_decision() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, old_document, new_document = (uuid.uuid4() for _ in range(4))
    old_job = _job(
        tenant=tenant,
        patient=patient,
        document=old_document,
        status="validation_failed",
    )
    new_job = _job(tenant=tenant, patient=patient, document=new_document)
    key = clinical_fact_key("lab_result", "durable-prior-fact")
    assert key is not None
    prior = _candidate(
        tenant=tenant,
        patient=patient,
        document=old_document,
        job=old_job.id,
        fact_key=key,
    )

    def decision() -> ExtractionDecisionRecord:
        return ExtractionDecisionRecord(
            id=uuid.uuid4(),
            decision_contract_version="1.0",
            evidence_contract_version="1.0",
            evidence_id=prior.evidence_id,
            patient_id=patient,
            tenant_id=tenant,
            organization_id=tenant,
            source_document_id=old_document,
            job_id=old_job.id,
            workflow_id="qualification-workflow",
            request_id=f"decision-{uuid.uuid4()}",
            attempt_id=f"{old_job.id}:1",
            lane="SOURCE_ONLY",
            reason_codes=["AUTO_COMMIT_DISABLED"],
            policy_version="phase1-production/1.0",
            policy_configuration_hash="c" * 64,
            evidence_digest="d" * 64,
            evaluated_at=datetime.now(timezone.utc),
            evaluator_version="qualification/1.0",
            auto_commit_feature_enabled=False,
            created_at=datetime.now(timezone.utc),
        )

    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add_all(
                [
                    _document(tenant=tenant, patient=patient, document_id=old_document),
                    _document(tenant=tenant, patient=patient, document_id=new_document),
                    old_job,
                    new_job,
                ]
            )
            await db.flush()
            db.add_all([prior, decision()])
            await db.flush()
            with patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=_live_capability()),
            ):
                await create_source_relationship(
                    db,
                    source_document_id=new_document,
                    related_document_id=old_document,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
            await db.commit()

        async with factory() as db:
            current = await db.get(ExtractionJob, new_job.id)
            unsupported = await pipeline_orchestrator._resolve_source_predecessors(
                db, job=current, candidates=[{"clinical_fact_key": key}]
            )
            assert unsupported[0][3] == frozenset(
                {EvidenceIssue.SUPERSESSION_UNRESOLVED}
            )
            await db.rollback()

        async with factory() as db:
            durable = await db.get(ExtractionJob, old_job.id)
            durable.status = "source_only"
            await db.commit()

        async with factory() as db:
            current = await db.get(ExtractionJob, new_job.id)
            resolved = await pipeline_orchestrator._resolve_source_predecessors(
                db, job=current, candidates=[{"clinical_fact_key": key}]
            )
            assert resolved[0][0] == str(prior.evidence_id)
            assert resolved[0][2] is not None
            assert resolved[0][3] == frozenset()
            await db.commit()

        async with factory() as db:
            db.add(decision())
            await db.commit()
        async with factory() as db:
            current = await db.get(ExtractionJob, new_job.id)
            ambiguous = await pipeline_orchestrator._resolve_source_predecessors(
                db, job=current, candidates=[{"clinical_fact_key": key}]
            )
            assert ambiguous[0][3] == frozenset({EvidenceIssue.SUPERSESSION_UNRESOLVED})
            await db.rollback()

        async with factory() as db:
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_conflict_creation_reorders_and_grows_members_idempotently() -> (
    None
):
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, document_id, job_id = (uuid.uuid4() for _ in range(4))
    key = clinical_fact_key("lab_result", "synthetic-concurrent-fact")
    assert key is not None
    candidate_ids: list[uuid.UUID] = []

    async def persist(member_ids: list[uuid.UUID]):
        async with factory() as db:
            members = (
                (
                    await db.execute(
                        select(ExtractionCandidateRecord).where(
                            ExtractionCandidateRecord.id.in_(member_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_id = {row.id: row for row in members}
            conflict = await persist_conflict_set(
                db,
                tenant_id=tenant,
                patient_id=patient,
                job_id=job_id,
                source_document_id=document_id,
                field_name="lab_result",
                fact_key=key,
                candidates=[by_id[item] for item in member_ids],
                created_at=datetime.now(timezone.utc),
            )
            await db.commit()
            return conflict.id

    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add(_document(tenant=tenant, patient=patient, document_id=document_id))
            db.add(
                ExtractionJob(
                    id=job_id,
                    patient_id=patient,
                    tenant_id=tenant,
                    uploader_id="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    consent_request_id="qualification-consent",
                    document_id=document_id,
                    document_type="application/pdf",
                    status="source_only",
                    request_id=f"qualification-{uuid.uuid4()}",
                    attempt_count=1,
                    retryable=False,
                    version=1,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            candidates = [
                _candidate(
                    tenant=tenant,
                    patient=patient,
                    document=document_id,
                    job=job_id,
                    fact_key=key,
                )
                for _ in range(3)
            ]
            candidate_ids = [row.id for row in candidates]
            db.add_all(candidates)
            await db.commit()

        conflict_ids = await asyncio.gather(
            persist([candidate_ids[0], candidate_ids[1]]),
            persist([candidate_ids[2], candidate_ids[1], candidate_ids[0]]),
        )
        assert conflict_ids[0] == conflict_ids[1]
        async with factory() as db:
            assert (
                await db.execute(
                    select(func.count(ExtractionConflictRecord.id)).where(
                        ExtractionConflictRecord.job_id == job_id,
                        ExtractionConflictRecord.clinical_fact_key == key,
                    )
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    select(func.count(ExtractionConflictMemberRecord.id)).where(
                        ExtractionConflictMemberRecord.conflict_id == conflict_ids[0]
                    )
                )
            ).scalar_one() == 3
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a1_provenance_tables_are_db_immutable_and_member_pair_is_exact() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, old_document, new_document, job_id = (
        uuid.uuid4() for _ in range(5)
    )
    key = clinical_fact_key("lab_result", "immutable-provenance-fact")
    assert key is not None
    protected: dict[str, uuid.UUID] = {}
    audit_patients = {patient}
    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add_all(
                [
                    _document(tenant=tenant, patient=patient, document_id=old_document),
                    _document(tenant=tenant, patient=patient, document_id=new_document),
                    _job(tenant=tenant, patient=patient, document=old_document),
                    ExtractionJob(
                        id=job_id,
                        patient_id=patient,
                        tenant_id=tenant,
                        uploader_id="qualification-reviewer",
                        authorization_provider_id="qualification-reviewer",
                        consent_request_id="qualification-workflow",
                        document_id=new_document,
                        document_type="application/pdf",
                        status="source_only",
                        request_id=f"qualification-{uuid.uuid4()}",
                        attempt_count=1,
                        retryable=False,
                        version=1,
                        created_at=datetime.now(timezone.utc),
                    ),
                ]
            )
            await db.flush()
            candidates = [
                _candidate(
                    tenant=tenant,
                    patient=patient,
                    document=new_document,
                    job=job_id,
                    fact_key=key,
                )
                for _ in range(4)
            ]
            db.add_all(candidates)
            await db.flush()
            conflict = await persist_conflict_set(
                db,
                tenant_id=tenant,
                patient_id=patient,
                job_id=job_id,
                source_document_id=new_document,
                field_name="lab_result",
                fact_key=key,
                candidates=candidates[:2],
                created_at=datetime.now(timezone.utc),
            )
            with patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=_live_capability()),
            ):
                relation = await create_source_relationship(
                    db,
                    source_document_id=new_document,
                    related_document_id=old_document,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
            await db.commit()
            protected["document_source_relationships"] = relation.id
            protected["extraction_conflicts"] = conflict.id
            protected["extraction_conflict_members"] = (
                (
                    await db.execute(
                        select(ExtractionConflictMemberRecord.id).where(
                            ExtractionConflictMemberRecord.conflict_id == conflict.id
                        )
                    )
                )
                .scalars()
                .first()
            )

        async with factory() as db:
            db.add(
                ExtractionConflictMemberRecord(
                    id=uuid.uuid4(),
                    conflict_id=protected["extraction_conflicts"],
                    candidate_id=candidates[2].id,
                    evidence_id=candidates[3].evidence_id,
                    created_at=datetime.now(timezone.utc),
                )
            )
            with pytest.raises(IntegrityError) as mismatch:
                await db.flush()
            assert getattr(mismatch.value.orig, "sqlstate", None) == "23503"
            await db.rollback()

        provider, resolution_patient, case_id, session_id = await _case(factory)
        audit_patients.add(resolution_patient)
        async with factory() as db:
            case = await db.get(AdjudicationCaseRecord, case_id)
            resolution_conflict = ExtractionConflictRecord(
                id=uuid.uuid4(),
                tenant_id=case.tenant_id,
                patient_id=case.patient_id,
                job_id=case.job_id,
                source_document_id=case.source_document_id,
                field_name="heart_rate",
                clinical_fact_key="b" * 64,
                created_at=datetime.now(timezone.utc),
            )
            db.add(resolution_conflict)
            await db.commit()
        async with factory() as db:
            with patch("app.services.adjudication._live_access", AsyncMock()):
                submission = await submit_case(
                    db,
                    case_id=case_id,
                    provider=provider,
                    review_session_id=session_id,
                    outcome=AdjudicationOutcome.ACCEPTED,
                    fields=_payload(datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)),
                    reason_codes=[AdjudicationReasonCode.SOURCE_VERIFIED],
                    idempotency_key=f"submit-{uuid.uuid4().hex}",
                    resolved_conflict_ids=[resolution_conflict.id],
                )
                await db.commit()
                protected["adjudication_conflict_resolutions"] = (
                    await db.execute(
                        select(AdjudicationConflictResolutionRecord.id).where(
                            AdjudicationConflictResolutionRecord.submission_id
                            == submission.id
                        )
                    )
                ).scalar_one()

        for table_name, row_id in protected.items():
            for statement in (
                f"UPDATE public.{table_name} SET created_at = created_at WHERE id = :id",
                f"DELETE FROM public.{table_name} WHERE id = :id",
            ):
                async with factory() as db:
                    with pytest.raises(DBAPIError) as immutable:
                        await db.execute(text(statement), {"id": row_id})
                    assert getattr(immutable.value.orig, "sqlstate", None) == "55000"
                    await db.rollback()

        async with factory() as db:
            for audit_patient in audit_patients:
                await db.execute(
                    text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                    {"patient": str(audit_patient)},
                )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_9_current_submission_must_resolve_conflict() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider, patient, case_id, session_id = await _case(factory)
        frozen = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        async with factory() as db:
            case = await db.get(AdjudicationCaseRecord, case_id)
            assert case is not None
            conflict = ExtractionConflictRecord(
                id=uuid.uuid4(),
                tenant_id=case.tenant_id,
                patient_id=case.patient_id,
                job_id=case.job_id,
                source_document_id=case.source_document_id,
                field_name="heart_rate",
                clinical_fact_key="a" * 64,
                created_at=datetime.now(timezone.utc),
            )
            db.add(conflict)
            await db.commit()
            conflict_id = conflict.id

        async with factory() as db:
            with patch("app.services.adjudication._live_access", AsyncMock()):
                with pytest.raises(
                    AdjudicationError,
                    match="ADJUDICATION_UNRESOLVED_CLINICAL_CONFLICT",
                ):
                    await submit_case(
                        db,
                        case_id=case_id,
                        provider=provider,
                        review_session_id=session_id,
                        outcome=AdjudicationOutcome.ACCEPTED,
                        fields=_payload(frozen),
                        reason_codes=[AdjudicationReasonCode.SOURCE_VERIFIED],
                        idempotency_key=f"submit-{uuid.uuid4().hex}",
                    )
                await db.rollback()

        async with factory() as db:
            with patch("app.services.adjudication._live_access", AsyncMock()):
                accepted = await submit_case(
                    db,
                    case_id=case_id,
                    provider=provider,
                    review_session_id=session_id,
                    outcome=AdjudicationOutcome.ACCEPTED,
                    fields=_payload(frozen),
                    reason_codes=[AdjudicationReasonCode.SOURCE_VERIFIED],
                    idempotency_key=f"submit-{uuid.uuid4().hex}",
                    resolved_conflict_ids=[conflict_id],
                )
                await db.commit()
                accepted_id = accepted.id

        async with factory() as db:
            with patch("app.services.adjudication._live_access", AsyncMock()):
                with pytest.raises(
                    AdjudicationError,
                    match="ADJUDICATION_UNRESOLVED_CLINICAL_CONFLICT",
                ):
                    await submit_case(
                        db,
                        case_id=case_id,
                        provider=provider,
                        review_session_id=session_id,
                        outcome=AdjudicationOutcome.ACCEPTED,
                        fields=_payload(frozen, value=72.0),
                        reason_codes=[AdjudicationReasonCode.CORRECTED_AGAINST_SOURCE],
                        idempotency_key=f"submit-{uuid.uuid4().hex}",
                        supersedes_submission_id=accepted_id,
                    )
                await db.rollback()

        async with factory() as db:
            with patch("app.services.adjudication._live_access", AsyncMock()):
                superseded = await submit_case(
                    db,
                    case_id=case_id,
                    provider=provider,
                    review_session_id=session_id,
                    outcome=AdjudicationOutcome.ACCEPTED,
                    fields=_payload(frozen, value=73.0),
                    reason_codes=[AdjudicationReasonCode.CORRECTED_AGAINST_SOURCE],
                    idempotency_key=f"submit-{uuid.uuid4().hex}",
                    supersedes_submission_id=accepted_id,
                    resolved_conflict_ids=[conflict_id],
                )
                committed = await commit_submission(
                    db,
                    submission_id=superseded.id,
                    provider=provider,
                    review_session_id=session_id,
                    before_clinical_mutation=AsyncMock(return_value=provider),
                )
                assert committed.clinical_committed_at is not None
                assert (
                    await db.execute(
                        select(
                            func.count(AdjudicationConflictResolutionRecord.id)
                        ).where(
                            AdjudicationConflictResolutionRecord.submission_id.in_(
                                [accepted_id, superseded.id]
                            )
                        )
                    )
                ).scalar_one() == 2
                await db.commit()
        # This suite owns the audit rows staged by its synthetic case. Remove
        # them so later global outbox-worker qualifications cannot claim this
        # test's deliberately unprocessed events.
        async with factory() as db:
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_commit_and_supersession_use_one_case_lock_order() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owned_patients: list[uuid.UUID] = []
    try:
        for run in range(3):
            provider, patient, case_id, session_id = await _case(factory)
            owned_patients.append(patient)
            frozen = datetime(2026, 8, 14, 12, run, tzinfo=timezone.utc)
            async with factory() as db:
                with patch("app.services.adjudication._live_access", AsyncMock()):
                    accepted = await submit_case(
                        db,
                        case_id=case_id,
                        provider=provider,
                        review_session_id=session_id,
                        outcome=AdjudicationOutcome.ACCEPTED,
                        fields=_payload(frozen),
                        reason_codes=[AdjudicationReasonCode.SOURCE_VERIFIED],
                        idempotency_key=f"submit-{uuid.uuid4().hex}",
                    )
                    await db.commit()
                    accepted_id = accepted.id

            async def supersede():
                async with factory() as db:
                    try:
                        row = await submit_case(
                            db,
                            case_id=case_id,
                            provider=provider,
                            review_session_id=session_id,
                            outcome=AdjudicationOutcome.ACCEPTED,
                            fields=_payload(frozen, value=75.0 + run),
                            reason_codes=[
                                AdjudicationReasonCode.CORRECTED_AGAINST_SOURCE
                            ],
                            idempotency_key=f"submit-{uuid.uuid4().hex}",
                            supersedes_submission_id=accepted_id,
                        )
                        await db.commit()
                        return ("PASS", row.id)
                    except AdjudicationError as exc:
                        await db.rollback()
                        return (exc.code, None)

            async def commit_original():
                async with factory() as db:
                    try:
                        row = await commit_submission(
                            db,
                            submission_id=accepted_id,
                            provider=provider,
                            review_session_id=session_id,
                            before_clinical_mutation=AsyncMock(return_value=provider),
                        )
                        await db.commit()
                        return ("PASS", row.id)
                    except AdjudicationError as exc:
                        await db.rollback()
                        return (exc.code, None)

            with patch("app.services.adjudication._live_access", AsyncMock()):
                results = await asyncio.gather(supersede(), commit_original())
            assert sum(result[0] == "PASS" for result in results) == 1
            assert all(
                result[0]
                in {
                    "PASS",
                    "ADJUDICATION_ALREADY_COMMITTED",
                    "ADJUDICATION_NOT_ACCEPTED",
                }
                for result in results
            )
            async with factory() as db:
                case = await db.get(AdjudicationCaseRecord, case_id)
                if case.accepted_submission_id != accepted_id:
                    assert case.clinical_committed_at is None

        async with factory() as db:
            for patient in owned_patients:
                await db.execute(
                    text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                    {"patient": str(patient)},
                )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_23_production_upload_revalidates_related_source() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient, related_id, unauthorized_id = (uuid.uuid4() for _ in range(4))
    provider = _provider(tenant)
    actor_id = provider.actor_uid
    capability = SimpleNamespace(
        request_id="qualification-workflow",
        allowed_operations={DocumentProcessingOperation.UPLOAD_DOCUMENT.value},
    )

    class _Storage:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def put_document(self, data, *, tenant_id, patient_id, mime_type):
            digest = hashlib.sha256(data).hexdigest()
            return StoredDocument(
                storage_ref=f"synthetic://{digest}",
                content_hash=digest,
                size=len(data),
                mime_type=mime_type,
                object_key=digest,
            )

        async def delete_document(self, storage_ref, *, tenant_id, patient_id):
            self.deleted.append(storage_ref)

    storage = _Storage()

    async def upload(db, *, related: uuid.UUID, suffix: bytes):
        return await upload_pipeline_document(
            request=Request({"type": "http", "method": "POST", "path": "/"}),
            background_tasks=BackgroundTasks(),
            patient_id=patient,
            file=UploadFile(
                file=BytesIO(b"%PDF-1.7\n" + suffix),
                filename="synthetic.pdf",
                headers=Headers({"content-type": "application/pdf"}),
            ),
            source_system="SYNTHETIC_POLICY_FIXTURE",
            related_document_id=related,
            source_relation_type="SUPERSEDES",
            provider=provider,
            x_consent_token="synthetic-capability",
            idempotency_key=f"upload-{uuid.uuid4()}",
            db=db,
        )

    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add_all(
                [
                    _document(
                        tenant=tenant,
                        patient=patient,
                        document_id=related_id,
                        uploader_id=actor_id,
                    ),
                    _document(
                        tenant=tenant,
                        patient=patient,
                        document_id=unauthorized_id,
                        uploader_id="other-provider",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    _job(
                        tenant=tenant,
                        patient=patient,
                        document=related_id,
                        provider_id=actor_id,
                    ),
                    _job(
                        tenant=tenant,
                        patient=patient,
                        document=unauthorized_id,
                        provider_id="other-provider",
                    ),
                ]
            )
            await db.commit()

        with (
            patch(
                "app.api.v2.pipeline_routes.authorize_document_processing",
                AsyncMock(return_value=capability),
            ),
            patch(
                "app.api.v2.pipeline_routes.get_document_extraction_config",
                return_value=SimpleNamespace(provider="synthetic"),
            ),
            patch(
                "app.api.v2.pipeline_routes.get_document_storage",
                return_value=storage,
            ),
            patch(
                "app.api.v2.pipeline_routes.append_audit_log_or_503",
                AsyncMock(return_value=True),
            ),
            patch(
                "app.api.v2.pipeline_routes.current_audit_context",
                return_value=SimpleNamespace(),
            ),
            # This Scenario 23 test qualifies related-source binding. The real
            # initiation-assurance capture is covered by the clinical auth and
            # delegated gate suites.
            patch(
                "app.api.v2.pipeline_routes.capture_clinical_initiation_assurance",
                AsyncMock(
                    return_value=SimpleNamespace(
                        initiated_at=datetime.now(timezone.utc),
                        authentication_method=SimpleNamespace(value="PROVIDER_SESSION"),
                        mfa_verified_at=datetime.now(timezone.utc),
                        assurance_policy_version=(
                            "clinical-contact-email-and-phone/v1"
                        ),
                    )
                ),
            ),
            patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=capability),
            ),
        ):
            async with factory() as db:
                response = await upload(db, related=related_id, suffix=b"authorized")
                assert response["status"] == "extraction_pending"
                assert response["duplicate"] is False

            async with factory() as db:
                with pytest.raises(HTTPException) as failure:
                    await upload(db, related=unauthorized_id, suffix=b"unauthorized")
                assert failure.value.status_code == 403
                assert failure.value.detail["error_code"] == (
                    "SOURCE_RELATION_PROVIDER_MISMATCH"
                )

        async with factory() as db:
            relations = (
                (
                    await db.execute(
                        select(DocumentSourceRelationshipRecord).where(
                            DocumentSourceRelationshipRecord.tenant_id == tenant,
                            DocumentSourceRelationshipRecord.patient_id == patient,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(relations) == 1
            assert relations[0].related_document_id == related_id
            assert len(storage.deleted) == 1
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_23_source_relations_are_append_only_and_cycle_safe() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient = uuid.uuid4(), uuid.uuid4()
    first_id, second_id, third_id = (uuid.uuid4() for _ in range(3))
    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add_all(
                [
                    _document(tenant=tenant, patient=patient, document_id=first_id),
                    _document(tenant=tenant, patient=patient, document_id=second_id),
                    _document(tenant=tenant, patient=patient, document_id=third_id),
                    _job(tenant=tenant, patient=patient, document=first_id),
                    _job(tenant=tenant, patient=patient, document=second_id),
                    _job(tenant=tenant, patient=patient, document=third_id),
                ]
            )
            await db.flush()
            with patch(
                "app.services.clinical_evidence_integrity."
                "validate_live_document_processing_request",
                AsyncMock(return_value=_live_capability()),
            ):
                first_edge = await create_source_relationship(
                    db,
                    source_document_id=second_id,
                    related_document_id=first_id,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
                same_edge = await create_source_relationship(
                    db,
                    source_document_id=second_id,
                    related_document_id=first_id,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
                assert first_edge.id == same_edge.id
                await create_source_relationship(
                    db,
                    source_document_id=third_id,
                    related_document_id=second_id,
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.ADDENDUM_TO,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
                with pytest.raises(ClinicalEvidenceIntegrityError, match="CYCLE"):
                    await create_source_relationship(
                        db,
                        source_document_id=first_id,
                        related_document_id=third_id,
                        tenant_id=tenant,
                        patient_id=patient,
                        relation_type=SourceRelationType.SUPERSEDES,
                        workflow_id="qualification-workflow",
                        created_by="qualification-reviewer",
                        authorization_provider_id="qualification-reviewer",
                        authorization_hospital_id=tenant,
                        consent_request_id="qualification-workflow",
                        created_at=datetime.now(timezone.utc),
                    )
            assert (
                await db.execute(
                    select(func.count(DocumentSourceRelationshipRecord.id)).where(
                        DocumentSourceRelationshipRecord.tenant_id == tenant,
                        DocumentSourceRelationshipRecord.patient_id == patient,
                    )
                )
            ).scalar_one() == 2
            await db.commit()

        async with factory() as db:
            invalid = DocumentSourceRelationshipRecord(
                id=uuid.uuid4(),
                tenant_id=tenant,
                patient_id=patient,
                source_document_id=first_id,
                related_document_id=first_id,
                relation_type="UNKNOWN",
                workflow_id="qualification-workflow",
                created_by="qualification-reviewer",
                created_at=datetime.now(timezone.utc),
            )
            db.add(invalid)
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE patient_id = :patient"),
                {"patient": str(patient)},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_23_source_graph_depth_overflow_fails_closed() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, patient = uuid.uuid4(), uuid.uuid4()
    documents = [uuid.uuid4() for _ in range(66)]
    try:
        async with factory() as db:
            await _seed_identity_graph(db, tenant=tenant, patient=patient)
            db.add_all(
                [
                    _document(tenant=tenant, patient=patient, document_id=document)
                    for document in documents
                ]
            )
            await db.flush()
            db.add_all(
                [
                    _job(tenant=tenant, patient=patient, document=document)
                    for document in documents
                ]
            )
            await db.flush()
            db.add_all(
                [
                    DocumentSourceRelationshipRecord(
                        id=uuid.uuid4(),
                        tenant_id=tenant,
                        patient_id=patient,
                        source_document_id=documents[index],
                        related_document_id=documents[index + 1],
                        relation_type=SourceRelationType.ADDENDUM_TO.value,
                        workflow_id="qualification-workflow",
                        created_by="qualification-reviewer",
                        created_at=datetime.now(timezone.utc),
                    )
                    for index in range(1, 65)
                ]
            )
            await db.commit()

        async with factory() as db:
            with (
                patch(
                    "app.services.clinical_evidence_integrity."
                    "validate_live_document_processing_request",
                    AsyncMock(return_value=_live_capability()),
                ),
                pytest.raises(
                    ClinicalEvidenceIntegrityError,
                    match="SOURCE_RELATION_GRAPH_TOO_DEEP",
                ),
            ):
                await create_source_relationship(
                    db,
                    source_document_id=documents[0],
                    related_document_id=documents[1],
                    tenant_id=tenant,
                    patient_id=patient,
                    relation_type=SourceRelationType.SUPERSEDES,
                    workflow_id="qualification-workflow",
                    created_by="qualification-reviewer",
                    authorization_provider_id="qualification-reviewer",
                    authorization_hospital_id=tenant,
                    consent_request_id="qualification-workflow",
                    created_at=datetime.now(timezone.utc),
                )
            await db.rollback()
            assert (
                await db.execute(
                    select(func.count(DocumentSourceRelationshipRecord.id)).where(
                        DocumentSourceRelationshipRecord.tenant_id == tenant,
                        DocumentSourceRelationshipRecord.patient_id == patient,
                    )
                )
            ).scalar_one() == 64
    finally:
        await engine.dispose()
