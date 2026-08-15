"""Runtime regressions for encrypted identity-discrepancy quarantine."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    DemoExtractionProvider,
    ExtractionProviderResult,
)
from app.ai.identity_decision import IdentityDecisionState
from app.models.ai_models import (
    ExtractedMedicalDocument,
    ProviderFieldEvidence,
)
from app.models.extraction_decision import DecisionLane, DecisionReason
from app.models.field_evidence import EvidenceIssue, NormalizedBoundingBox
from app.models.pipeline import (
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionJob,
)
from app.services.adjudication import AdjudicationError, create_case
from app.services.crypto_kms import EncryptedField
from app.services.extraction_decision_engine import evaluate_extraction_evidence
from app.services.pipeline_orchestrator import process_extraction_job
from app.api.v2.pipeline_routes import CommitJobRequest, commit_extraction_job

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
CANONICAL_NAME = "SYNTHETIC_CANONICAL_PERSON_42"
WRONG_NAME = "SYNTHETIC_WRONG_PERSON_99"


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _DB:
    def __init__(self, results):
        self.results = list(results)
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement):
        return _Result(self.results.pop(0))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _encrypted_placeholder(field_name: str) -> str:
    return EncryptedField(
        ciphertext=b"synthetic-ciphertext",
        iv=b"0" * 12,
        field_name=field_name,
        dek_version=1,
        algorithm="AES-256-GCM",
    ).serialize()


def _provider_evidence(
    field_name: str,
    value: str,
    *,
    source_type: str = "QUERY_RESULT",
    block_id: str,
) -> ProviderFieldEvidence:
    return ProviderFieldEvidence(
        canonical_field_name=field_name,
        raw_value=value,
        source_text=value,
        page_number=0,
        bounding_box=NormalizedBoundingBox(left=0.1, top=0.1, right=0.4, bottom=0.2),
        field_confidence=0.98,
        provider_name="synthetic-provider",
        provider_api_version="synthetic-v1",
        extraction_timestamp=NOW,
        evidence_hash=(block_id.encode().hex() + "0" * 64)[:64],
        source_type=source_type,
        source_block_ids=(block_id,),
    )


def _clinical_evidence() -> ProviderFieldEvidence:
    return _provider_evidence(
        "hba1c", "7.2 %", source_type="QUERY_RESULT", block_id="clinical-1"
    )


def _document(*identity_evidence: ProviderFieldEvidence, clinical: bool = True):
    evidence = list(identity_evidence)
    if clinical:
        evidence.append(_clinical_evidence())
    document = ExtractedMedicalDocument(
        patient_name="",
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
        extraction_confidence=0.99,
        field_evidence=evidence,
    )
    return document


class _FixtureDemoProvider(DemoExtractionProvider):
    def __init__(self, document: ExtractedMedicalDocument) -> None:
        self._result = ExtractionProviderResult(
            document=document,
            provider_adapter="demo",
            provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
            provider_model_version="synthetic-v1",
            response_complete=True,
            provider_attempt_traces=(),
        )
        self.extract_bytes = AsyncMock(return_value=self._result)


def _job_and_document():
    patient_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    tenant_id = uuid.UUID("20000000-0000-4000-8000-000000000002")
    document_id = uuid.UUID("30000000-0000-4000-8000-000000000003")
    document = DocumentStorage(
        id=document_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="40000000-0000-4000-8000-000000000004",
        storage_ref="local-test://identity-document",
        content_type="application/pdf",
        size=128,
        content_hash="a" * 64,
        uploaded_at=NOW,
    )
    job = ExtractionJob(
        id=uuid.UUID("50000000-0000-4000-8000-000000000005"),
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="40000000-0000-4000-8000-000000000004",
        authorization_provider_id="40000000-0000-4000-8000-000000000004",
        consent_request_id="workflow-identity-1",
        document_id=document_id,
        document_type="lab_report",
        status="queued",
        request_id="request-identity-1",
        attempt_count=0,
        retryable=False,
        created_at=NOW,
    )
    return job, document


async def _run_pipeline(
    extracted: ExtractedMedicalDocument,
    *,
    identity_asserted: bool,
    canonical_available: bool = True,
    rerun: bool = False,
):
    job, document = _job_and_document()
    vault = SimpleNamespace(
        patient_name=(
            _encrypted_placeholder("patient_name") if canonical_available else None
        ),
        phone=_encrypted_placeholder("phone") if canonical_available else None,
        aadhaar_abha_id=(
            _encrypted_placeholder("aadhaar_abha_id") if canonical_available else None
        ),
    )
    db_results = [job, document]
    if identity_asserted:
        db_results.append(vault)
    if rerun:
        db_results.append(job)
    db = _DB(db_results)

    storage = SimpleNamespace(get_document_bytes=AsyncMock(return_value=b"%PDF-1.7"))
    extractor = _FixtureDemoProvider(extracted)

    async def decrypt(_patient_id, field_name, _encrypted, _db):
        if field_name == "patient_name":
            return CANONICAL_NAME
        return "SYNTHETIC_CANONICAL_REFERENCE"

    async def encrypt(_patient_id, field_name, _value, _db):
        return EncryptedField(
            ciphertext=b"encrypted-retained-clinical-evidence",
            iv=b"1" * 12,
            field_name=field_name,
            dek_version=1,
            algorithm="AES-256-GCM",
        )

    kms = SimpleNamespace(
        decrypt_field=AsyncMock(side_effect=decrypt),
        encrypt_field=AsyncMock(side_effect=encrypt),
    )
    decisions = []

    async def route(_db, *, evidence, policy, evaluated_at, **kwargs):
        decision = evaluate_extraction_evidence(
            evidence=evidence,
            policy=policy,
            decision_id_factory=lambda: str(uuid.uuid4()),
            evaluated_at=evaluated_at,
        )
        decisions.append(decision)
        if decision.lane is DecisionLane.QUARANTINE:
            assert kwargs["quarantine_review_deadline"] is not None
        return SimpleNamespace(
            routing=SimpleNamespace(lane=decision.lane.value),
            decision=SimpleNamespace(
                reason_codes=[reason.value for reason in decision.reasons]
            ),
        )

    route_mock = AsyncMock(side_effect=route)
    append_audit = AsyncMock(return_value=True)
    enqueue_audit = AsyncMock(return_value=None)
    with (
        patch(
            "app.services.pipeline_orchestrator.get_document_storage",
            return_value=storage,
        ),
        patch(
            "app.services.pipeline_orchestrator.get_medical_document_extractor",
            return_value=extractor,
        ),
        patch(
            "app.services.pipeline_orchestrator.get_document_extraction_config",
            return_value=SimpleNamespace(
                provider="demo", provider_max_attempts=2, job_max_attempts=2
            ),
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
            "app.services.pipeline_orchestrator.evaluate_and_persist_lane",
            route_mock,
        ),
        patch(
            "app.services.pipeline_orchestrator.append_audit_log_or_503",
            append_audit,
        ),
        patch(
            "app.services.pipeline_orchestrator.enqueue_audit_event",
            enqueue_audit,
        ),
    ):
        result = await process_extraction_job(str(job.id), db)
        second_result = await process_extraction_job(str(job.id), db) if rerun else None

    return SimpleNamespace(
        result=result,
        second_result=second_result,
        job=job,
        document=document,
        db=db,
        extractor=extractor,
        kms=kms,
        route=route_mock,
        decisions=decisions,
        append_audit=append_audit,
        enqueue_audit=enqueue_audit,
    )


def _identity(name: str, *, source_type: str, block_id: str):
    return _provider_evidence(
        "patient_name", name, source_type=source_type, block_id=block_id
    )


@pytest.mark.asyncio
async def test_scenario_8_identity_discrepancy_is_encrypted_quarantined_and_idempotent(
    caplog,
):
    with caplog.at_level(logging.ERROR, logger="nexa_logger"):
        run = await _run_pipeline(
            _document(
                _identity(WRONG_NAME, source_type="QUERY_RESULT", block_id="wrong-1")
            ),
            identity_asserted=True,
            rerun=True,
        )

    candidates = [
        item for item in run.db.added if isinstance(item, ExtractionCandidateRecord)
    ]
    assert run.result["status"] == run.job.status == "quarantined"
    assert run.result["quarantine_count"] == 1
    assert run.job.error_code == "EXTRACTED_IDENTITY_MISMATCH"
    assert run.second_result == {
        "job_id": str(run.job.id),
        "status": "quarantined",
        "idempotent": True,
    }
    assert run.job.attempt_count == 1
    assert run.extractor.extract_bytes.await_count == 1
    assert run.route.await_count == 1
    assert run.append_audit.await_count == 1
    assert run.enqueue_audit.await_count == 1
    assert len(candidates) == 1

    candidate = candidates[0]
    assert candidate.patient_id == run.job.patient_id == run.document.patient_id
    assert candidate.tenant_id == run.job.tenant_id == run.document.tenant_id
    assert candidate.source_document_id == run.job.document_id == run.document.id
    assert candidate.authorization_provider_id == run.job.authorization_provider_id
    assert candidate.lane == "QUARANTINE"
    assert "IDENTITY_MISMATCH" in candidate.reason_codes
    assert candidate.encrypted_raw_value
    assert candidate.encrypted_source_text
    assert not hasattr(candidate, "raw_value")
    assert "7.2 %" not in candidate.encrypted_raw_value
    assert run.decisions[0].lane is DecisionLane.QUARANTINE
    assert DecisionReason.IDENTITY_MISMATCH in run.decisions[0].reasons
    routed_evidence = run.route.await_args.kwargs["evidence"]
    assert EvidenceIssue.IDENTITY_MISMATCH in routed_evidence.identity.issues
    assert routed_evidence.identity.patient_id == str(run.job.patient_id)

    provider = SimpleNamespace(
        actor_uid=run.job.authorization_provider_id,
        hospital=SimpleNamespace(hospital_id=run.job.tenant_id),
        affiliation=SimpleNamespace(roles=["clinician"]),
    )
    adjudication_db = _DB(
        [SimpleNamespace(lane="QUARANTINE", status="QUARANTINE_PENDING")]
    )
    with pytest.raises(AdjudicationError) as adjudication_error:
        await create_case(
            adjudication_db,
            provider=provider,
            idempotency_key="identity-quarantine-case-1",
            review_session_id="identity-review-session-1",
            routing_id=uuid.uuid4(),
        )
    assert adjudication_error.value.code == "ADJUDICATION_ROUTE_INELIGIBLE"
    assert adjudication_db.added == []

    commit_db = _DB([run.job])
    with pytest.raises(HTTPException) as commit_error:
        await commit_extraction_job(
            str(run.job.id),
            CommitJobRequest(patient_id=str(run.job.patient_id)),
            provider=provider,
            x_consent_token=None,
            db=commit_db,
        )
    assert commit_error.value.detail == {
        "error_code": "QUARANTINED_JOB_NOT_COMMITTABLE"
    }
    assert commit_db.added == []

    audit_projection = repr(
        (run.append_audit.await_args_list, run.enqueue_audit.await_args_list)
    )
    emitted_logs = "\n".join(record.getMessage() for record in caplog.records)
    for sensitive_marker in (CANONICAL_NAME, WRONG_NAME):
        assert sensitive_marker not in audit_projection
        assert sensitive_marker not in emitted_logs
    routed_metadata = run.enqueue_audit.await_args.kwargs["metadata"]
    assert routed_metadata["identity_state"] == "IDENTITY_DISCREPANCY"
    assert routed_metadata["reason_code"] == "IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    ("assertions", "expected_state"),
    [
        (
            (
                _identity(
                    CANONICAL_NAME, source_type="QUERY_RESULT", block_id="exact-query"
                ),
                _identity(
                    WRONG_NAME, source_type="KEY_VALUE_SET", block_id="wrong-form"
                ),
            ),
            IdentityDecisionState.IDENTITY_CONFLICTING,
        ),
        (
            (
                _identity(
                    "SYNTHETIC_WRONG_QUERY",
                    source_type="QUERY_RESULT",
                    block_id="wrong-query",
                ),
                _identity(
                    "SYNTHETIC_WRONG_FORM",
                    source_type="KEY_VALUE_SET",
                    block_id="wrong-form-2",
                ),
            ),
            IdentityDecisionState.IDENTITY_DISCREPANCY,
        ),
    ],
)
@pytest.mark.asyncio
async def test_query_and_form_assertions_have_no_source_precedence(
    assertions, expected_state
):
    run = await _run_pipeline(_document(*assertions), identity_asserted=True)

    assert run.job.status == "quarantined"
    assert run.job.error_code == "EXTRACTED_IDENTITY_MISMATCH"
    metadata = run.enqueue_audit.await_args.kwargs["metadata"]
    assert metadata["identity_state"] == expected_state.value
    assert run.decisions[0].lane is DecisionLane.QUARANTINE
    assert DecisionReason.IDENTITY_MISMATCH in run.decisions[0].reasons


@pytest.mark.asyncio
async def test_no_document_identity_assertion_preserves_source_only_behavior():
    run = await _run_pipeline(_document(), identity_asserted=False)

    assert run.result["status"] == run.job.status == "source_only"
    assert run.job.error_code is None
    assert run.route.await_count == 1
    assert run.decisions[0].lane is DecisionLane.SOURCE_ONLY
    assert run.enqueue_audit.await_args.kwargs["metadata"]["identity_state"] is None


@pytest.mark.asyncio
async def test_missing_canonical_reference_uses_identity_unavailable_quarantine():
    run = await _run_pipeline(
        _document(
            _identity(WRONG_NAME, source_type="QUERY_RESULT", block_id="missing-ref")
        ),
        identity_asserted=True,
        canonical_available=False,
    )

    candidate = next(
        item for item in run.db.added if isinstance(item, ExtractionCandidateRecord)
    )
    assert run.job.status == "quarantined"
    assert run.job.error_code == "EXTRACTED_IDENTITY_UNAVAILABLE"
    assert candidate.lane == "QUARANTINE"
    assert "IDENTITY_UNAVAILABLE" in candidate.reason_codes
    assert run.enqueue_audit.await_args.kwargs["metadata"]["identity_state"] == (
        "IDENTITY_INSUFFICIENT"
    )


@pytest.mark.asyncio
async def test_zero_clinical_candidates_with_identity_mismatch_still_quarantines():
    run = await _run_pipeline(
        _document(
            _identity(WRONG_NAME, source_type="QUERY_RESULT", block_id="wrong-empty"),
            clinical=False,
        ),
        identity_asserted=True,
    )

    assert run.result["status"] == run.job.status == "quarantined"
    assert run.job.error_code == "EXTRACTED_IDENTITY_MISMATCH"
    assert run.route.await_count == 0
    assert [
        item for item in run.db.added if isinstance(item, ExtractionCandidateRecord)
    ] == []
    metadata = run.enqueue_audit.await_args.kwargs["metadata"]
    assert metadata["lane"] == "QUARANTINE"
    assert metadata["candidate_count"] == 0
    assert metadata["identity_state"] == "IDENTITY_DISCREPANCY"
    assert metadata["reason_code"] == "IDENTITY_MISMATCH"


@pytest.mark.asyncio
async def test_zero_candidate_identity_quarantine_rejects_document_adjudication():
    run = await _run_pipeline(
        _document(
            _identity(
                WRONG_NAME,
                source_type="QUERY_RESULT",
                block_id="wrong-document-review",
            ),
            clinical=False,
        ),
        identity_asserted=True,
    )
    original_binding = (
        run.job.patient_id,
        run.job.tenant_id,
        run.job.document_id,
    )
    assert run.job.status == "quarantined"
    assert [
        item for item in run.db.added if isinstance(item, ExtractionCandidateRecord)
    ] == []

    provider = SimpleNamespace(
        actor_uid=run.job.authorization_provider_id,
        hospital=SimpleNamespace(hospital_id=run.job.tenant_id),
        affiliation=SimpleNamespace(roles=["clinician"]),
    )
    adjudication_db = _DB([run.job, run.document, 0])
    with pytest.raises(AdjudicationError) as adjudication_error:
        await create_case(
            adjudication_db,
            provider=provider,
            idempotency_key="identity-zero-candidate-case-1",
            review_session_id="identity-zero-review-session-1",
            job_id=run.job.id,
        )

    assert adjudication_error.value.code == "ADJUDICATION_JOB_INELIGIBLE"
    assert adjudication_db.added == []
    assert run.job.status == "quarantined"
    assert (
        run.job.patient_id,
        run.job.tenant_id,
        run.job.document_id,
    ) == original_binding
