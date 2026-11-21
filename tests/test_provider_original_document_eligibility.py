"""Adversarial unit tests for internal source-graph eligibility.

No test grants consent, opens file bytes, or claims live disclosure coverage.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.services import provider_original_document_eligibility as service


def _graph():
    patient_id, document_id, import_id, source_id = (
        uuid.uuid4() for _ in range(4)
    )
    ref = SimpleNamespace(
        id=document_id,
        patient_id=patient_id,
        extraction_job_id=None,
        document_type="LAB_REPORT",
        storage_ref="local+encrypted://private-test-object",
    )
    imported = SimpleNamespace(
        id=import_id,
        patient_id=patient_id,
        source_document_id=source_id,
        final_record_id=document_id,
        final_record_type="DOCUMENT_REFERENCE",
        status="COMPLETED",
        completed_at=datetime.now(timezone.utc),
        review_completed_at=datetime.now(timezone.utc),
        timeline_event_id=uuid.uuid4(),
        error_code=None,
        category="LAB_REPORT",
        content_hash="a" * 64,
    )
    source = SimpleNamespace(
        id=source_id,
        patient_id=patient_id,
        tenant_id=None,
        source_system="patient_self",
        upload_purpose="patient_external_record_import",
        content_type="application/pdf",
        size=1024,
        storage_ref=ref.storage_ref,
        content_hash="a" * 64,
    )
    return patient_id, document_id, ref, imported, source


def _validate(graph):
    patient, document, ref, imported, source = graph
    return service.validate_original_document_graph(
        patient_id=patient,
        document_id=document,
        reference=ref,
        imported=imported,
        source=source,
    )


def test_valid_graph_returns_only_internal_identity_references():
    graph = _graph()
    result = _validate(graph)
    assert result.patient_id == graph[0]
    assert result.document_id == graph[1]
    assert result.import_id == graph[3].id
    assert result.source_document_id == graph[4].id
    assert not hasattr(result, "storage_ref")
    assert not hasattr(result, "content_hash")
    assert "private-test-object" not in repr(result)
    assert str(graph[0]) not in repr(result)


@pytest.mark.parametrize(
    ("row_index", "field", "value"),
    [
        (2, "id", uuid.UUID(int=1)),
        (2, "patient_id", uuid.UUID(int=2)),
        (2, "extraction_job_id", uuid.UUID(int=3)),
        (3, "patient_id", uuid.UUID(int=4)),
        (3, "final_record_id", uuid.UUID(int=5)),
        (3, "final_record_type", "MEDICATION"),
        (3, "source_document_id", uuid.UUID(int=6)),
        (3, "status", "READY_TO_SAVE"),
        (3, "status", "CANCELLED"),
        (3, "status", "FAILED_TERMINAL"),
        (3, "completed_at", None),
        (3, "review_completed_at", None),
        (3, "timeline_event_id", None),
        (3, "error_code", "SOURCE_MALWARE_DETECTED"),
        (3, "error_code", "IDENTITY_CONFLICT"),
        (3, "category", "PRESCRIPTION"),
        (3, "content_hash", None),
        (3, "content_hash", "b" * 64),
        (3, "content_hash", "invalid-hash"),
        (4, "patient_id", uuid.UUID(int=7)),
        (4, "tenant_id", uuid.UUID(int=8)),
        (4, "source_system", "provider_upload"),
        (4, "upload_purpose", "other"),
        (4, "content_type", "text/html"),
        (4, "size", 0),
        (4, "size", -1),
        (4, "content_hash", None),
        (4, "storage_ref", "local+encrypted://wrong-object"),
        (4, "storage_ref", "erased://patient-external-record-source"),
        (2, "storage_ref", "erased://patient-external-record"),
    ],
)
def test_rejects_incomplete_unsafe_or_wrong_owner_graph(row_index, field, value):
    graph = _graph()
    setattr(graph[row_index], field, value)
    with pytest.raises(HTTPException) as caught:
        _validate(graph)
    assert caught.value.status_code == 404
    assert caught.value.detail == {"error_code": "ORIGINAL_DOCUMENT_NOT_ELIGIBLE"}


@pytest.mark.asyncio
async def test_resolver_scopes_query_and_rechecks_erasure(monkeypatch):
    patient, document, ref, imported, source = _graph()
    lifecycle = AsyncMock()
    monkeypatch.setattr(service, "assert_patient_external_record_access_active", lifecycle)
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.all.return_value = [(ref, imported, source)]
    result = await service.resolve_eligible_original_document(
        db, patient_id=patient, document_id=document
    )
    assert result.source_document_id == source.id
    statement = db.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "document_references.patient_id" in sql
    assert "patient_external_record_imports.patient_id" in sql
    assert "document_storage.patient_id" in sql
    assert "document_storage.tenant_id IS NULL" in sql
    assert "document_references.id" in sql
    assert patient in compiled.params.values()
    assert document in compiled.params.values()
    assert lifecycle.await_count == 2


@pytest.mark.parametrize("count", [0, 2])
@pytest.mark.asyncio
async def test_resolver_rejects_missing_and_ambiguous_graph(monkeypatch, count):
    patient, document, ref, imported, source = _graph()
    monkeypatch.setattr(
        service, "assert_patient_external_record_access_active", AsyncMock()
    )
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.all.return_value = [(ref, imported, source)] * count
    with pytest.raises(HTTPException) as caught:
        await service.resolve_eligible_original_document(
            db, patient_id=patient, document_id=document
        )
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_erasure_denial_prevents_metadata_fetch(monkeypatch):
    patient, document, *_ = _graph()
    lifecycle = AsyncMock(side_effect=HTTPException(410, detail="retired"))
    monkeypatch.setattr(service, "assert_patient_external_record_access_active", lifecycle)
    db = AsyncMock()
    with pytest.raises(HTTPException) as caught:
        await service.resolve_eligible_original_document(
            db, patient_id=patient, document_id=document
        )
    assert caught.value.status_code == 410
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_erasure_during_resolution_denies_return(monkeypatch):
    patient, document, ref, imported, source = _graph()
    lifecycle = AsyncMock(side_effect=[None, HTTPException(410, detail="retired")])
    monkeypatch.setattr(service, "assert_patient_external_record_access_active", lifecycle)
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.all.return_value = [(ref, imported, source)]
    with pytest.raises(HTTPException) as caught:
        await service.resolve_eligible_original_document(
            db, patient_id=patient, document_id=document
        )
    assert caught.value.status_code == 410


@pytest.mark.asyncio
async def test_metadata_dependency_failure_is_value_free(monkeypatch):
    patient, document, *_ = _graph()
    monkeypatch.setattr(
        service, "assert_patient_external_record_access_active", AsyncMock()
    )
    db = AsyncMock()
    db.execute.side_effect = SQLAlchemyError("sensitive internal details")
    with pytest.raises(HTTPException) as caught:
        await service.resolve_eligible_original_document(
            db, patient_id=patient, document_id=document
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == {"error_code": "ORIGINAL_DOCUMENT_METADATA_UNAVAILABLE"}
