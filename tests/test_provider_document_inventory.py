"""Source-level qualification for the consent-gated provider document inventory.

No real patient source files, DB, Redis, or external infrastructure are used.
These tests do not qualify an original-document viewer.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response

from app.api.v2 import provider_document_routes as routes


def _doc(*, patient_id: uuid.UUID, when: datetime, doc_id: uuid.UUID | None = None):
    return SimpleNamespace(
        id=doc_id or uuid.uuid4(),
        patient_id=patient_id,
        document_type="LAB_REPORT",
        uploaded_at=when,
        storage_ref="private-object-reference-do-not-disclose",
    )


def test_document_cursor_roundtrip_and_fail_closed_input():
    dt = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    doc_id = uuid.uuid4()
    cursor = routes.encode_document_cursor(dt, doc_id)
    assert routes.decode_document_cursor(cursor) == (dt, doc_id)
    assert routes.decode_document_cursor(None) is None

    for bad in ("", "not-base64%%", "a" * 513, base64.urlsafe_b64encode(
        b'{"t":"2026-09-10T12:00:00","id":"00000000-0000-0000-0000-000000000001"}'
    ).decode("ascii")):
        with pytest.raises(HTTPException) as caught:
            routes.decode_document_cursor(bad)
        assert caught.value.status_code == 422
        assert caught.value.detail["error_code"] == "INVALID_DOCUMENT_CURSOR"


def test_source_presence_does_not_assert_clinician_verification():
    patient = uuid.uuid4()
    doc = _doc(patient_id=patient, when=datetime.now(timezone.utc))
    imported = SimpleNamespace(status="COMPLETED")
    item = routes.project_document(doc, imported)
    assert item.source_origin == "patient_imported_external"
    assert item.verification_status == "patient_reviewed_not_clinician_verified"
    assert item.original_access == "separate_authorization_required"
    assert "storage_ref" not in item.model_dump()
    assert "original_filename" not in item.model_dump()

    unknown = routes.project_document(doc, None)
    assert unknown.source_origin == "not_established"
    assert unknown.verification_status == "not_established"
    assert unknown.original_access == "separate_authorization_required"


@pytest.mark.asyncio
async def test_list_is_patient_scoped_keyset_paginated_and_no_store(monkeypatch):
    patient_id = uuid.uuid4()
    at = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    first = _doc(patient_id=patient_id, when=at, doc_id=uuid.UUID(int=100))
    second = _doc(patient_id=patient_id, when=at, doc_id=uuid.UUID(int=99))

    gate = AsyncMock()
    monkeypatch.setattr(routes, "assert_patient_external_record_access_active", gate)
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [
        (first, SimpleNamespace(status="COMPLETED")), (second, None)
    ]
    db.execute.return_value = result

    response = Response()
    page = await routes.list_provider_patient_documents(
        response=response, patient_id=str(patient_id), limit=1, cursor=None,
        provider=MagicMock(), capability=object(), db=db,
    )
    assert len(page.items) == 1
    assert page.items[0].document_id == first.id
    assert routes.decode_document_cursor(page.next_cursor) == (at, first.id)
    assert response.headers["Cache-Control"] == "private, no-store"
    gate.assert_awaited_once_with(db, patient_id=str(patient_id))

    compiled = str(db.execute.call_args.args[0].compile())
    assert "document_references.patient_id" in compiled
    assert "patient_external_record_imports.status" in compiled
    assert "document_references.uploaded_at DESC" in compiled
    assert "document_references.id DESC" in compiled

    # A cursor never carries patient identity or source storage refs.
    assert str(patient_id) not in page.next_cursor
    assert "private-object" not in page.model_dump_json()


@pytest.mark.asyncio
async def test_erasure_or_retirement_blocks_listing_before_database_query(monkeypatch):
    patient_id = uuid.uuid4()
    gate = AsyncMock(side_effect=HTTPException(410, detail={"error_code": "PATIENT_DATA_ERASED"}))
    monkeypatch.setattr(routes, "assert_patient_external_record_access_active", gate)
    db = AsyncMock()
    with pytest.raises(HTTPException) as caught:
        await routes.list_provider_patient_documents(
            response=Response(), patient_id=str(patient_id),
            limit=20, cursor=None, provider=MagicMock(), capability=object(), db=db,
        )
    assert caught.value.status_code == 410
    db.execute.assert_not_awaited()


def test_read_route_requires_both_provider_capability_and_routine_consent():
    path = next(
        route for route in routes.router.routes
        if getattr(route, "path", "").endswith("/documents")
    )
    deps = [d.call for d in path.dependant.dependencies]
    assert any(
        getattr(d, "__name__", "") == "require_clinical_capability"
        or getattr(d, "__name__", "") == "_clinical_capability_gate"
        for d in deps
    ) or any("capability" in repr(d) for d in deps)
    assert any(getattr(d, "__name__", "") == "_consent_gate" for d in deps)
    assert set(path.methods) == {"GET"}
    assert not any("/source" in getattr(x, "path", "") for x in routes.router.routes)
