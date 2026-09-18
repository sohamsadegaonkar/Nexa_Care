from __future__ import annotations

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
import pytest

import app.services.patient_external_record_extraction as extraction_module

from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    DemoExtractionProvider,
    ExtractionProviderResult,
    ProviderResponseError,
)
from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.services.patient_external_record_extraction import (
    _candidate_evidence_id,
    _failure_status,
    _load_owned_import_for_update,
    _reviewable_field_evidence,
    _source_digest,
    _validated_provider_result,
    process_patient_external_record,
    retry_patient_external_record,
)


def _evidence(field: str, value: str, *, evidence_hash: str | None = None):
    return ProviderFieldEvidence(
        canonical_field_name=field,
        raw_value=value,
        source_text=f"source for {field}",
        page_number=0,
        bounding_box=None,
        field_confidence=0.91,
        provider_name="demo",
        provider_api_version="demo-v1",
        extraction_timestamp=datetime.now(timezone.utc),
        evidence_hash=evidence_hash,
        source_type="QUERY_RESULT",
    )


def _result(*evidence: ProviderFieldEvidence, adapter: str = "demo"):
    document = ExtractedMedicalDocument(
        patient_name="compat identity",
        aadhaar_abha_id="compat id",
        phone="compat phone",
        diagnoses=["summary-only diagnosis"],
        lab_results=["summary-only lab"],
        prescriptions=["summary-only prescription"],
        extraction_confidence=0.82,
        field_evidence=list(evidence),
    )
    return ExtractionProviderResult(
        document=document,
        provider_adapter=adapter,
        provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
        provider_model_version=None,
        response_complete=True,
        provider_attempt_traces=(),
    )


def test_process_service_accepts_only_patient_import_authority() -> None:
    parameter_names = set(inspect.signature(process_patient_external_record).parameters)
    assert parameter_names == {"db", "patient_id", "import_id"}
    assert {
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "consent_request_id",
        "clinical_access_session_id",
    }.isdisjoint(parameter_names)


def test_process_service_contains_required_fail_closed_boundaries() -> None:
    source = inspect.getsource(process_patient_external_record)
    assert "check_erasure_registry" in source
    assert "get_patient_document_bytes" in source
    assert "_source_digest" in source
    assert "get_medical_document_extractor" in source
    assert "get_encryption_provider" in source
    assert "REVIEW_REQUIRED" in source
    assert "final_record_id" not in source
    assert "timeline_event_id" not in source
    assert "authorize_document_processing" not in source
    assert "ClinicalAccessSession" not in source


def test_provider_result_must_match_server_configured_adapter() -> None:
    extractor = DemoExtractionProvider()
    valid = _result(_evidence("hba1c", "6.1%"))
    assert (
        _validated_provider_result(
            configured_provider="demo",
            extractor=extractor,
            result=valid,
        )
        is valid
    )

    forged = _result(_evidence("hba1c", "6.1%"), adapter="remote")
    with pytest.raises(ProviderResponseError):
        _validated_provider_result(
            configured_provider="demo",
            extractor=extractor,
            result=forged,
        )


def test_identity_fields_and_summary_arrays_never_become_review_candidates() -> None:
    result = _result(
        _evidence("patient_name", "Someone"),
        _evidence("phone", "0000000000"),
        _evidence("aadhaar_abha_id", "identifier"),
        _evidence("hba1c", "6.1%"),
        _evidence("diagnosis", ""),
    )

    reviewable = _reviewable_field_evidence(result)
    assert [item.canonical_field_name for item in reviewable] == ["hba1c"]
    assert all("summary-only" not in item.raw_value for item in reviewable)


def test_candidate_evidence_id_is_server_owned_deterministic_and_value_free() -> None:
    import_id = uuid.uuid4()
    first = _evidence("hba1c", "6.1%", evidence_hash="provider-hash-1")
    second = _evidence("hba1c", "9.9%", evidence_hash="provider-hash-1")

    assert _candidate_evidence_id(import_id, first, 1) == _candidate_evidence_id(
        import_id, second, 1
    )
    assert _candidate_evidence_id(import_id, first, 1) != _candidate_evidence_id(
        import_id, first, 2
    )


def test_failure_state_mapping_is_closed_and_patient_safe() -> None:
    assert _failure_status(True) == "FAILED_RETRYABLE"
    assert _failure_status(False) == "FAILED_TERMINAL"


def test_source_integrity_digest_is_sha256_and_deterministic() -> None:
    data = b"synthetic-patient-owned-source"
    assert len(_source_digest(data)) == 64
    assert _source_digest(data) == _source_digest(data)
    assert _source_digest(data) != _source_digest(data + b"-changed")


@pytest.mark.asyncio
async def test_owned_import_lookup_binds_patient_and_import_under_row_lock() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    patient_id = uuid.uuid4()
    import_id = uuid.uuid4()

    await _load_owned_import_for_update(
        db,
        patient_id=patient_id,
        import_id=import_id,
    )

    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    bound_values = set(compiled.params.values())
    assert patient_id in bound_values
    assert import_id in bound_values
    assert "patient_external_record_imports.patient_id" in str(statement)
    assert "FOR UPDATE" in str(statement).upper()


@pytest.mark.asyncio
async def test_retired_patient_is_denied_before_source_or_extractor_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    row = SimpleNamespace(status="UPLOADED")

    async def owned_import(*args, **kwargs):
        return row

    monkeypatch.setattr(
        extraction_module,
        "_load_owned_import_for_update",
        owned_import,
    )
    db.get.return_value = SimpleNamespace(is_deleted=True)

    with pytest.raises(HTTPException) as exc_info:
        await process_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_RECORD_RETIRED"}
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_runtime_failure_rolls_back_with_value_free_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()

    async def unexpected_failure(*args, **kwargs):
        raise RuntimeError("synthetic-sensitive-clinical-value")

    monkeypatch.setattr(
        extraction_module,
        "_load_owned_import_for_update",
        unexpected_failure,
    )

    with pytest.raises(HTTPException) as exc_info:
        await process_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error_code": "EXTRACTION_UNAVAILABLE",
        "retryable": True,
    }
    assert "synthetic-sensitive-clinical-value" not in str(exc_info.value.detail)
    db.rollback.assert_awaited_once()



def test_retry_service_accepts_only_patient_import_authority() -> None:
    parameter_names = set(inspect.signature(retry_patient_external_record).parameters)
    assert parameter_names == {"db", "patient_id", "import_id"}
    assert {
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "consent_request_id",
        "clinical_access_session_id",
        "treatment_token",
    }.isdisjoint(parameter_names)


@pytest.mark.asyncio
async def test_retry_accepts_only_failed_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_id = str(uuid.uuid4())
    import_id = uuid.uuid4()
    row = SimpleNamespace(status="FAILED_RETRYABLE", retryable=True)
    processed = SimpleNamespace(status="REVIEW_REQUIRED", retryable=False)
    db = AsyncMock()

    gate = AsyncMock(return_value=None)
    lookup = AsyncMock(return_value=row)
    process = AsyncMock(return_value=processed)
    monkeypatch.setattr(
        extraction_module, "assert_patient_external_record_access_active", gate
    )
    monkeypatch.setattr(extraction_module, "_load_owned_import_for_update", lookup)
    monkeypatch.setattr(extraction_module, "process_patient_external_record", process)

    result = await retry_patient_external_record(
        db,
        patient_id=patient_id,
        import_id=import_id,
    )

    assert result is processed
    gate.assert_awaited_once_with(db, patient_id=patient_id)
    lookup.assert_awaited_once_with(
        db,
        patient_id=uuid.UUID(patient_id),
        import_id=import_id,
    )
    process.assert_awaited_once_with(
        db,
        patient_id=patient_id,
        import_id=import_id,
    )
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        "UPLOADED",
        "PROCESSING",
        "REVIEW_REQUIRED",
        "READY_TO_SAVE",
        "COMPLETED",
        "FAILED_TERMINAL",
        "CANCELLED",
    ],
)
async def test_retry_rejects_every_non_retryable_state(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    db = AsyncMock()
    row = SimpleNamespace(status=state, retryable=False)
    process = AsyncMock()
    monkeypatch.setattr(
        extraction_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        extraction_module,
        "_load_owned_import_for_update",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(extraction_module, "process_patient_external_record", process)

    with pytest.raises(HTTPException) as exc_info:
        await retry_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "error_code": "EXTERNAL_RECORD_RETRY_NOT_AVAILABLE",
        "retryable": False,
    }
    process.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_requires_retryable_flag_even_in_failed_retryable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    monkeypatch.setattr(
        extraction_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        extraction_module,
        "_load_owned_import_for_update",
        AsyncMock(return_value=SimpleNamespace(status="FAILED_RETRYABLE", retryable=False)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await retry_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_wrong_patient_or_guessed_import_is_not_found_and_unlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    monkeypatch.setattr(
        extraction_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        extraction_module,
        "_load_owned_import_for_update",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await retry_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == {"error_code": "EXTERNAL_RECORD_NOT_FOUND"}
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    [
        {"error_code": "PATIENT_DATA_ERASED"},
        {"error_code": "PATIENT_RECORD_RETIRED"},
    ],
)
async def test_retry_fails_before_lookup_for_inactive_patient_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    detail: dict[str, str],
) -> None:
    lookup = AsyncMock()
    monkeypatch.setattr(
        extraction_module,
        "assert_patient_external_record_access_active",
        AsyncMock(side_effect=HTTPException(status_code=410, detail=detail)),
    )
    monkeypatch.setattr(extraction_module, "_load_owned_import_for_update", lookup)

    with pytest.raises(HTTPException) as exc_info:
        await retry_patient_external_record(
            AsyncMock(),
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == detail
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    [
        {"error_code": "SOURCE_DOCUMENT_UNAVAILABLE", "retryable": True},
        {"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        {"error_code": "AUDIT_UNAVAILABLE", "retryable": True},
    ],
)
async def test_retry_propagates_fail_closed_process_failures(
    monkeypatch: pytest.MonkeyPatch,
    detail: dict[str, object],
) -> None:
    db = AsyncMock()
    monkeypatch.setattr(
        extraction_module,
        "assert_patient_external_record_access_active",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        extraction_module,
        "_load_owned_import_for_update",
        AsyncMock(
            return_value=SimpleNamespace(status="FAILED_RETRYABLE", retryable=True)
        ),
    )
    monkeypatch.setattr(
        extraction_module,
        "process_patient_external_record",
        AsyncMock(side_effect=HTTPException(status_code=503, detail=detail)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await retry_patient_external_record(
            db,
            patient_id=str(uuid.uuid4()),
            import_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == detail
