"""Fail-closed delegated clinical authorization regressions."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import ANY, AsyncMock, patch

import pytest

from app.core.document_processing_gate import (
    DelegatedClinicalTrustError,
    recheck_delegated_document_processing_trust,
)
from app.models.pipeline import DocumentStorage, ExtractionJob
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import ClinicalEligibilityDenialCode
from app.services.pipeline_orchestrator import process_extraction_job
from app.services.provider_job_lifecycle import (
    ProviderReconciliationClaim,
    ProviderReconciliationOutcome,
    ReconciliationOutcomeType,
)
from app.services.textract_async_runtime import make_textract_reconciliation_callback


def _job(**overrides):
    values = {
        "id": uuid4(),
        "patient_id": uuid4(),
        "tenant_id": uuid4(),
        "authorization_provider_id": str(uuid4()),
        "consent_request_id": str(uuid4()),
        "authorization_initiated_at": datetime.now(timezone.utc),
        "authorization_authentication_method": "PROVIDER_SESSION",
        "authorization_mfa_verified_at": datetime.now(timezone.utc),
        "authorization_assurance_policy_version": "clinical-contact-email-and-phone/v1",
        "status": "extracting",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_missing_delegated_initiation_assurance_is_rejected_without_live_work() -> None:
    job = _job(authorization_initiated_at=None)
    with pytest.raises(DelegatedClinicalTrustError) as exc_info:
        asyncio.run(recheck_delegated_document_processing_trust(job=job, db=object()))
    assert exc_info.value.code == "DELEGATED_INITIATION_ASSURANCE_REQUIRED"


def test_live_document_consent_loss_denies_the_delegated_step() -> None:
    job = _job()
    denied = SimpleNamespace(
        allowed=False,
        denial_code=ClinicalEligibilityDenialCode.DELEGATED_WORKFLOW_BINDING_INVALID,
    )
    with (
        patch(
            "app.core.document_processing_gate.validate_live_document_processing_request",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.document_processing_gate.ClinicalEligibilityService.evaluate_delegated",
            AsyncMock(return_value=denied),
        ) as evaluate,
    ):
        with pytest.raises(DelegatedClinicalTrustError) as exc_info:
            asyncio.run(
                recheck_delegated_document_processing_trust(job=job, db=object())
            )
    assert exc_info.value.code == "DELEGATED_WORKFLOW_BINDING_INVALID"
    assert evaluate.await_args.args[4] is ClinicalCapability.DOCUMENTS_PROCESS


def test_quarantined_workflow_is_not_delegated_runnable_after_trust_restoration() -> (
    None
):
    job = _job(status="quarantined")
    with pytest.raises(DelegatedClinicalTrustError) as exc_info:
        asyncio.run(recheck_delegated_document_processing_trust(job=job, db=object()))
    assert exc_info.value.code == "DELEGATED_WORKFLOW_STATE_INVALID"


class _PipelineResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _PipelineDb:
    def __init__(self, job, document):
        self._results = [job, document]
        self.added = []

    async def execute(self, *_args, **_kwargs):
        return _PipelineResult(self._results.pop(0))

    async def commit(self):
        return None

    def add(self, value):
        self.added.append(value)


def test_initial_async_denial_happens_before_any_source_read() -> None:
    job = _job(status="extraction_pending")
    storage = SimpleNamespace(get_document_bytes=AsyncMock())
    denial = DelegatedClinicalTrustError("PROFESSIONAL_SUSPENDED")
    quarantine = AsyncMock(
        return_value=SimpleNamespace(
            id=job.id, status="quarantined", error_code=denial.code
        )
    )
    with (
        patch(
            "app.services.pipeline_orchestrator.recheck_delegated_document_processing_trust",
            AsyncMock(side_effect=denial),
        ),
        patch(
            "app.services.pipeline_orchestrator.get_document_storage",
            return_value=storage,
        ),
        patch(
            "app.services.pipeline_orchestrator.quarantine_delegated_clinical_trust_denial",
            quarantine,
        ),
    ):
        result = asyncio.run(
            process_extraction_job(str(job.id), _PipelineDb(job, None))
        )

    assert result["status"] == "quarantined"
    storage.get_document_bytes.assert_not_awaited()
    quarantine.assert_awaited_once_with(db=ANY, job_id=job.id, error_code=denial.code)


class _ReconciliationResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ReconciliationDb:
    def __init__(self, *values):
        self._values = list(values)

    async def execute(self, *_args, **_kwargs):
        return _ReconciliationResult(self._values.pop(0))


class _ReconciliationSessionFactory:
    def __init__(self, db):
        self._db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_args):
        return False


def _reconciliation_claim(row) -> ProviderReconciliationClaim:
    return ProviderReconciliationClaim(
        provider_attempt_id=row.id,
        job_id=row.job_id,
        provider_adapter="aws_textract",
        provider_contract_version="async-textract/1.0",
        provider_job_id=row.provider_job_id,
        client_request_token_digest="a" * 64,
        provider_request_fingerprint=row.provider_request_fingerprint,
        claimed_version=1,
        reconciliation_attempt_number=1,
        reconciliation_deadline_at=None,
    )


@pytest.mark.parametrize(
    "denial_code", ["PROFESSIONAL_SUSPENDED", "DELEGATED_WORKFLOW_BINDING_INVALID"]
)
def test_reconciliation_denial_prevents_source_prepare(
    denial_code: str,
) -> None:
    job = _job(status="extracting")
    row = SimpleNamespace(
        id=uuid4(),
        job_id=job.id,
        tenant_id=job.tenant_id,
        provider_job_id=None,
        provider_request_fingerprint="a" * 64,
    )
    stager = SimpleNamespace(prepare_for_attempt=AsyncMock(), delete=AsyncMock())
    provider = SimpleNamespace(start=AsyncMock(), check_status=AsyncMock())
    quarantine = AsyncMock(return_value=job)
    callback = make_textract_reconciliation_callback(
        session_factory=_ReconciliationSessionFactory(_ReconciliationDb(row, job)),
        provider=provider,
        stager=stager,
    )
    with (
        patch(
            "app.services.textract_async_runtime.recheck_delegated_document_processing_trust",
            AsyncMock(side_effect=DelegatedClinicalTrustError(denial_code)),
        ),
        patch(
            "app.services.textract_async_runtime.quarantine_delegated_clinical_trust_denial",
            quarantine,
        ),
    ):
        outcome = asyncio.run(callback(_reconciliation_claim(row)))

    assert outcome.outcome is ReconciliationOutcomeType.FAILED_TERMINAL
    stager.prepare_for_attempt.assert_not_awaited()
    provider.start.assert_not_awaited()
    quarantine.assert_awaited_once_with(db=ANY, job_id=job.id, error_code=denial_code)


def test_status_only_reconciliation_never_prepares_or_reads_source() -> None:
    job = _job(status="extracting")
    row = SimpleNamespace(
        id=uuid4(),
        job_id=job.id,
        tenant_id=job.tenant_id,
        provider_job_id="provider-job",
        provider_request_fingerprint="a" * 64,
    )
    stager = SimpleNamespace(prepare_for_attempt=AsyncMock(), delete=AsyncMock())
    expected = ProviderReconciliationOutcome(ReconciliationOutcomeType.IN_PROGRESS)
    provider = SimpleNamespace(
        start=AsyncMock(), check_status=AsyncMock(return_value=expected)
    )
    callback = make_textract_reconciliation_callback(
        session_factory=_ReconciliationSessionFactory(_ReconciliationDb(row, job)),
        provider=provider,
        stager=stager,
    )
    with patch(
        "app.services.textract_async_runtime.recheck_delegated_document_processing_trust",
        AsyncMock(return_value=None),
    ):
        outcome = asyncio.run(callback(_reconciliation_claim(row)))

    assert outcome is expected
    stager.prepare_for_attempt.assert_not_awaited()
    provider.start.assert_not_awaited()
    provider.check_status.assert_awaited_once_with(provider_job_id=row.provider_job_id)


@pytest.mark.parametrize(
    "denial_code",
    ["PROFESSIONAL_SUSPENDED", "DELEGATED_WORKFLOW_BINDING_INVALID"],
)
def test_sync_source_rechecks_immediately_before_read_and_denial_reads_nothing(
    denial_code: str,
) -> None:
    patient_id, tenant_id, document_id = (uuid.uuid4() for _ in range(3))
    now = datetime.now(timezone.utc)
    job = ExtractionJob(
        id=uuid.uuid4(),
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="synthetic-provider",
        document_id=document_id,
        document_type="application/pdf",
        status="queued",
        request_id="synthetic-source-race",
        created_at=now,
    )
    document = DocumentStorage(
        id=document_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="synthetic-provider",
        storage_ref="local://synthetic-source-race",
        content_type="application/pdf",
        size=1,
        content_hash="a" * 64,
        uploaded_at=now,
    )
    db = _PipelineDb(job, document)
    storage = SimpleNamespace(get_document_bytes=AsyncMock())
    extractor = SimpleNamespace(extract_bytes=AsyncMock())
    quarantine = AsyncMock(
        return_value=SimpleNamespace(
            id=job.id,
            status="quarantined",
            error_code=denial_code,
            retryable=False,
        )
    )
    with (
        patch(
            "app.services.pipeline_orchestrator.recheck_delegated_document_processing_trust",
            AsyncMock(side_effect=[None, DelegatedClinicalTrustError(denial_code)]),
        ) as recheck,
        patch(
            "app.services.pipeline_orchestrator.get_document_storage",
            return_value=storage,
        ),
        patch(
            "app.services.pipeline_orchestrator.get_document_extraction_config",
            return_value=SimpleNamespace(
                provider="demo", job_max_attempts=1, provider_max_attempts=1
            ),
        ),
        patch(
            "app.services.pipeline_orchestrator.get_medical_document_extractor",
            return_value=extractor,
        ),
        patch(
            "app.services.pipeline_orchestrator.append_audit_log_or_503",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.pipeline_orchestrator.quarantine_delegated_clinical_trust_denial",
            quarantine,
        ),
    ):
        result = asyncio.run(process_extraction_job(str(job.id), db))

    assert result == {
        "job_id": str(job.id),
        "status": "quarantined",
        "error_code": denial_code,
        "retryable": False,
    }
    assert recheck.await_count == 2
    storage.get_document_bytes.assert_not_awaited()
    extractor.extract_bytes.assert_not_awaited()
    assert db.added == []
    quarantine.assert_awaited_once_with(db=db, job_id=job.id, error_code=denial_code)


@pytest.mark.parametrize(
    "denial_code",
    ["PROFESSIONAL_SUSPENDED", "DELEGATED_WORKFLOW_BINDING_INVALID"],
)
def test_sync_submit_rechecks_after_source_before_external_call(
    denial_code: str,
) -> None:
    patient_id, tenant_id, document_id = (uuid.uuid4() for _ in range(3))
    now = datetime.now(timezone.utc)
    job = ExtractionJob(
        id=uuid.uuid4(),
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="synthetic-provider",
        document_id=document_id,
        document_type="application/pdf",
        status="queued",
        request_id="synthetic-race",
        created_at=now,
    )
    document = DocumentStorage(
        id=document_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="synthetic-provider",
        storage_ref="local://synthetic",
        content_type="application/pdf",
        size=1,
        content_hash="a" * 64,
        uploaded_at=now,
    )
    storage = SimpleNamespace(get_document_bytes=AsyncMock(return_value=b"%PDF"))
    extractor = SimpleNamespace(extract_bytes=AsyncMock())
    quarantine = AsyncMock(
        return_value=SimpleNamespace(
            id=job.id,
            status="quarantined",
            error_code=denial_code,
        )
    )
    with (
        patch(
            "app.services.pipeline_orchestrator.recheck_delegated_document_processing_trust",
            AsyncMock(
                side_effect=[
                    None,
                    None,
                    DelegatedClinicalTrustError(denial_code),
                ]
            ),
        ) as recheck,
        patch(
            "app.services.pipeline_orchestrator.get_document_storage",
            return_value=storage,
        ),
        patch(
            "app.services.pipeline_orchestrator.get_document_extraction_config",
            return_value=SimpleNamespace(
                provider="demo", job_max_attempts=1, provider_max_attempts=1
            ),
        ),
        patch(
            "app.services.pipeline_orchestrator.get_medical_document_extractor",
            return_value=extractor,
        ),
        patch(
            "app.services.pipeline_orchestrator.append_audit_log_or_503",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.pipeline_orchestrator.quarantine_delegated_clinical_trust_denial",
            quarantine,
        ),
    ):
        result = asyncio.run(
            process_extraction_job(str(job.id), _PipelineDb(job, document))
        )

    assert result == {
        "job_id": str(job.id),
        "status": "quarantined",
        "error_code": denial_code,
        "retryable": False,
    }
    storage.get_document_bytes.assert_awaited_once()
    assert recheck.await_count == 3
    extractor.extract_bytes.assert_not_awaited()
    quarantine.assert_awaited_once_with(db=ANY, job_id=job.id, error_code=denial_code)


def test_async_submission_and_handoff_have_mandatory_current_trust_boundaries() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "textract_async_runtime.py"
    ).read_text(encoding="utf-8")
    start = source[
        source.index("async def start_async_provider_attempt") : source.index(
            "def make_textract_reconciliation_callback"
        )
    ]
    reconciliation = source[
        source.index("def make_textract_reconciliation_callback") : source.index(
            "async def retrieve_and_complete_provider_attempt"
        )
    ]
    retrieval = source[
        source.index("async def retrieve_and_complete_provider_attempt") :
    ]

    assert "before_provider_submission: BeforeProviderSubmissionGuard" in start
    assert (
        start.index("staged = await stager.stage(prepared)")
        < start.index("await before_provider_submission()")
        < start.index("await provider.start(")
    )
    first_recheck = reconciliation.index(
        "await recheck_delegated_document_processing_trust(job=job, db=db)"
    )
    last_recheck = reconciliation.rindex(
        "await recheck_delegated_document_processing_trust(job=job, db=db)"
    )
    assert first_recheck < reconciliation.index("prepare_for_attempt(")
    assert (
        reconciliation.index("staged = await stager.stage(prepared)")
        < last_recheck
        < reconciliation.index("await provider.start(")
    )
    assert retrieval.index("await _recheck_attempt_trust(") < retrieval.index(
        "await provider.retrieve_complete_result("
    )
    assert retrieval.count("await _recheck_attempt_trust(") == 3
