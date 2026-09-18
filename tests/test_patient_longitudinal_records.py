"""
Comprehensive tests for Patient Longitudinal Health Record endpoints, keyset
cursor pagination, category filtering, clinical provenance, and IDOR protection.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v2.patient_record_routes import (
    _decode_keyset_cursor,
    _encode_keyset_cursor,
    _fetch_patient_longitudinal_timeline,
)
from app.core.database import get_db_session
from app.core.dependencies import get_scoped_session
from app.main import app
from app.models.patient_records import DocumentReference, Medication, Vitals


# ---------------------------------------------------------------------------
# Keyset Cursor Unit Tests
# ---------------------------------------------------------------------------


def test_keyset_cursor_encode_decode_roundtrip():
    dt = datetime(2026, 7, 17, 12, 34, 56, tzinfo=timezone.utc)
    ev_id = uuid.uuid4()
    cursor = _encode_keyset_cursor(dt, ev_id)
    assert isinstance(cursor, str)
    assert len(cursor) > 0

    decoded_dt, decoded_id = _decode_keyset_cursor(cursor)
    assert decoded_dt == dt
    assert decoded_id == str(ev_id)


def test_keyset_cursor_rejects_malformed_inputs():
    # Not valid base64
    with pytest.raises(HTTPException) as exc1:
        _decode_keyset_cursor("not_valid_b64!!!")
    assert exc1.value.status_code == 422

    # Valid base64 but not JSON
    bad_json = base64.urlsafe_b64encode(b"not json").decode()
    with pytest.raises(HTTPException) as exc2:
        _decode_keyset_cursor(bad_json)
    assert exc2.value.status_code == 422

    # Missing fields
    missing_fields = base64.urlsafe_b64encode(json.dumps({"t": "2026-07-17T00:00:00Z"}).encode()).decode()
    with pytest.raises(HTTPException) as exc3:
        _decode_keyset_cursor(missing_fields)
    assert exc3.value.status_code == 422

    # Bad timestamp
    bad_dt = base64.urlsafe_b64encode(json.dumps({"occurred_at": "bad-date", "id": str(uuid.uuid4())}).encode()).decode()
    with pytest.raises(HTTPException) as exc4:
        _decode_keyset_cursor(bad_dt)
    assert exc4.value.status_code == 422

    # Empty ID
    empty_id = base64.urlsafe_b64encode(json.dumps({"occurred_at": "2026-07-17T00:00:00Z", "id": "   "}).encode()).decode()
    with pytest.raises(HTTPException) as exc5:
        _decode_keyset_cursor(empty_id)
    assert exc5.value.status_code == 422


# ---------------------------------------------------------------------------
# Timeline Keyset Fetcher Unit Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_patient_longitudinal_timeline_empty():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute.return_value = mock_result

    pat_id = uuid.uuid4()
    events, next_cursor = await _fetch_patient_longitudinal_timeline(
        pat_id, db, cursor=None, category=None, limit=20
    )
    assert events == []
    assert next_cursor is None


@pytest.mark.asyncio
async def test_fetch_patient_longitudinal_timeline_category_filter():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute.return_value = mock_result

    pat_id = uuid.uuid4()
    # When filtering by 'vitals', vitals table + timeline_events table are checked (2 queries)
    events, next_cursor = await _fetch_patient_longitudinal_timeline(
        pat_id, db, cursor=None, category="vitals", limit=20
    )
    assert events == []
    assert db.execute.await_count == 2


# ---------------------------------------------------------------------------
# API Endpoints Integration Tests (using TestClient & Dependency Overrides)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def patient_uuid():
    return uuid.uuid4()


@pytest.fixture
def override_patient_auth(patient_uuid):
    app.dependency_overrides[get_scoped_session] = lambda: str(patient_uuid)
    yield patient_uuid
    app.dependency_overrides.pop(get_scoped_session, None)


def test_unauthenticated_request_fails(client):
    app.dependency_overrides.pop(get_scoped_session, None)
    response = client.get("/api/v2/patient/me/summary")
    assert response.status_code in (401, 403)


def test_idor_attempt_rejected(client, override_patient_auth, patient_uuid):
    other_patient = uuid.uuid4()
    with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
        response = client.get(
            f"/api/v2/patient/me/timeline?patient_id={other_patient}",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 403
        assert "does not match" in response.text


def test_patient_me_summary(client, override_patient_auth, patient_uuid):
    mock_db = AsyncMock()
    # Mock count queries and timeline fetch
    mock_result = MagicMock()
    mock_result.scalar.return_value = 0
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            response = client.get("/api/v2/patient/me/summary")
            assert response.status_code == 200
            data = response.json()
            assert data["patient_id"] == str(patient_uuid)
            assert "counts" in data
            assert "allergy_highlights" in data
            assert "active_medications" in data
            assert "latest_vitals" in data
            assert "recent_labs" in data
            assert "recent_reports" in data
            assert "recent_timeline_events" in data
            # Cache control headers check
            cache_ctrl = response.headers.get("Cache-Control", "")
            assert "no-store" in cache_ctrl
            assert "private" in cache_ctrl
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_me_timeline_invalid_category(client, override_patient_auth):
    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            response = client.get("/api/v2/patient/me/timeline?category=invalid_type")
            assert response.status_code == 400
            assert "INVALID_TIMELINE_CATEGORY" in response.text
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_me_records_overview(client, override_patient_auth, patient_uuid):
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 2
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            response = client.get("/api/v2/patient/me/records")
            assert response.status_code == 200
            data = response.json()
            assert data["patient_id"] == str(patient_uuid)
            assert "categories" in data
            assert len(data["categories"]) == 5
            category_keys = [c["category"] for c in data["categories"]]
            assert "vitals" in category_keys
            assert "medications" in category_keys
            assert "labs" in category_keys
            assert "allergies" in category_keys
            assert "documents" in category_keys
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_me_records_by_category(client, override_patient_auth, patient_uuid):
    mock_db = AsyncMock()
    mock_vital = Vitals(
        id=uuid.uuid4(),
        patient_id=patient_uuid,
        type="BP",
        value="120/80",
        unit="mmHg",
        recorded_at=datetime(2026, 7, 17, 10, 0, 0, tzinfo=timezone.utc),
        source="manual",
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_vital]
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            # Test valid category
            response = client.get("/api/v2/patient/me/records/vitals")
            assert response.status_code == 200
            data = response.json()
            assert data["category"] == "vitals"
            assert len(data["records"]) == 1
            assert data["records"][0]["type"] == "BP"
            assert data["records"][0]["value"] == "120/80"

            # Test invalid category
            bad_resp = client.get("/api/v2/patient/me/records/unknown_category")
            assert bad_resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_me_record_detail_vitals(client, override_patient_auth, patient_uuid):
    mock_db = AsyncMock()
    vital_id = uuid.uuid4()
    mock_vital = Vitals(
        id=vital_id,
        patient_id=patient_uuid,
        type="BP",
        value="120/80",
        unit="mmHg",
        recorded_at=datetime(2026, 7, 17, 10, 0, 0, tzinfo=timezone.utc),
        source="manual",
        source_document_id=None,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_vital
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            # Valid item
            response = client.get(f"/api/v2/patient/me/records/vitals/{vital_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["record_id"] == str(vital_id)
            assert data["category"] == "vitals"
            assert data["fields"]["type"] == "BP"
            assert data["fields"]["value"] == "120/80"
            assert data["provenance"]["source"] == "manual"
            assert data["provenance"]["source_display"] == "Clinician Recorded"

            # Non-existent item (404)
            mock_result.scalar_one_or_none.return_value = None
            nf_resp = client.get(f"/api/v2/patient/me/records/vitals/{uuid.uuid4()}")
            assert nf_resp.status_code == 404

            # Non-UUID record_id (422)
            bad_id_resp = client.get("/api/v2/patient/me/records/vitals/not-a-uuid")
            assert bad_id_resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_me_prescriptions(client, override_patient_auth, patient_uuid):
    mock_db = AsyncMock()
    med_id = uuid.uuid4()
    mock_med = Medication(
        id=med_id,
        patient_id=patient_uuid,
        name="Metformin",
        strength="500mg",
        frequency="Twice daily",
        prescribed_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        source="manual",
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_med]
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            response = client.get("/api/v2/patient/me/prescriptions")
            assert response.status_code == 200
            data = response.json()
            assert len(data["prescriptions"]) == 1
            assert data["prescriptions"][0]["medication_name"] == "Metformin"
            assert data["prescriptions"][0]["strength"] == "500mg"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_me_reports(client, override_patient_auth, patient_uuid):
    mock_db = AsyncMock()
    doc_id = uuid.uuid4()
    mock_doc = DocumentReference(
        id=doc_id,
        patient_id=patient_uuid,
        document_type="LAB_REPORT",
        uploaded_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc),
        storage_ref="internal-safe-ref",
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_doc]
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            response = client.get("/api/v2/patient/me/reports")
            assert response.status_code == 200
            data = response.json()
            assert len(data["reports"]) == 1
            assert data["reports"][0]["document_type"] == "LAB_REPORT"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_me_documents_safe_metadata_never_exposes_s3(
    client, override_patient_auth, patient_uuid
):
    mock_db = AsyncMock()
    doc_id = uuid.uuid4()
    mock_doc = DocumentReference(
        id=doc_id,
        patient_id=patient_uuid,
        document_type="DISCHARGE_SUMMARY",
        uploaded_at=datetime(2026, 3, 10, 8, 30, 0, tzinfo=timezone.utc),
        storage_ref="s3://nexa-care-protected-phi/private/raw/discharge_summary.pdf",
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            response = client.get(f"/api/v2/patient/me/documents/{doc_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["document_id"] == str(doc_id)
            assert data["patient_id"] == str(patient_uuid)
            assert data["document_type"] == "DISCHARGE_SUMMARY"
            assert data["is_owner"] is True
            assert data["view_authorized"] is True

            # CRITICAL SECURITY CHECK: Never leak storage_ref or raw S3 location
            assert "storage_ref" not in data
            assert "storage_path" not in data
            assert "s3://" not in response.text
            assert "protected-phi" not in response.text
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_external_records_projected_into_longitudinal_timeline(patient_uuid):
    mock_db = AsyncMock()
    rx_doc_id = uuid.uuid4()
    rx_doc = DocumentReference(
        id=rx_doc_id,
        patient_id=patient_uuid,
        document_type="PRESCRIPTION",
        uploaded_at=datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
        storage_ref="safe-storage-ref",
    )
    lab_doc_id = uuid.uuid4()
    lab_doc = DocumentReference(
        id=lab_doc_id,
        patient_id=patient_uuid,
        document_type="LAB_REPORT",
        uploaded_at=datetime(2026, 6, 2, 11, 0, 0, tzinfo=timezone.utc),
        storage_ref="safe-storage-ref",
    )

    # Empty for vitals, meds, labs, timeline_events, but returns docs
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    doc_result = MagicMock()
    doc_result.scalars.return_value.all.return_value = [lab_doc, rx_doc]

    def mock_execute(stmt):
        # check if querying DocumentReference
        s = str(stmt)
        res = MagicMock()
        if "document_references" in s:
            res.scalars.return_value.all.return_value = [lab_doc, rx_doc]
        else:
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute.side_effect = mock_execute

    # 1. Querying medications category should project prescription document
    med_events, _ = await _fetch_patient_longitudinal_timeline(
        str(patient_uuid), mock_db, category="medications"
    )
    rx_event = next(e for e in med_events if e["event_id"] == str(rx_doc_id))
    assert rx_event["source"] == "patient_uploaded"
    assert rx_event["source_display"] == "Imported by you from an external report"

    # 2. Querying labs category should project lab report document
    lab_events, _ = await _fetch_patient_longitudinal_timeline(
        str(patient_uuid), mock_db, category="labs"
    )
    lab_event = next(e for e in lab_events if e["event_id"] == str(lab_doc_id))
    assert lab_event["source"] == "patient_uploaded"
    assert lab_event["source_display"] == "Imported by you from an external report"

    # 3. Querying documents category should project both
    doc_events, _ = await _fetch_patient_longitudinal_timeline(
        str(patient_uuid), mock_db, category="documents"
    )
    assert len(doc_events) == 2


def test_prescriptions_includes_external_prescription_documents(
    client, override_patient_auth, patient_uuid
):
    mock_db = AsyncMock()
    med_id = uuid.uuid4()
    mock_med = Medication(
        id=med_id,
        patient_id=patient_uuid,
        name="Amoxicillin",
        strength="500mg",
        frequency="TID",
        prescribed_at=datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        source="manual",
    )
    doc_id = uuid.uuid4()
    mock_doc = DocumentReference(
        id=doc_id,
        patient_id=patient_uuid,
        document_type="PRESCRIPTION",
        uploaded_at=datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc),
        storage_ref="internal-safe-ref",
    )

    def mock_execute(stmt):
        s = str(stmt)
        res = MagicMock()
        if "patient_medications" in s:
            res.scalars.return_value.all.return_value = [mock_med]
        elif "document_references" in s:
            res.scalars.return_value.all.return_value = [mock_doc]
        else:
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute.side_effect = mock_execute

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            response = client.get("/api/v2/patient/me/prescriptions?include_external=true")
            assert response.status_code == 200
            data = response.json()
            assert len(data["prescriptions"]) == 2
            # Verify external prescription is present and clearly labeled
            ext_rx = next(p for p in data["prescriptions"] if p["prescription_id"] == str(doc_id))
            assert ext_rx["source"] == "patient_uploaded"
            assert ext_rx["source_display"] == "Imported by you from an external report"
            assert ext_rx["is_external_document"] is True
            assert "s3://" not in response.text
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_reports_filtering_by_document_type(
    client, override_patient_auth, patient_uuid
):
    mock_db = AsyncMock()
    lab_doc = DocumentReference(
        id=uuid.uuid4(),
        patient_id=patient_uuid,
        document_type="LAB_REPORT",
        uploaded_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc),
        storage_ref="internal-safe-ref",
    )

    def mock_execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = [lab_doc]
        return res

    mock_db.execute.side_effect = mock_execute

    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.append_audit_log_or_503", AsyncMock()):
            # Filter by lab report
            response = client.get("/api/v2/patient/me/reports?document_type=lab_report")
            assert response.status_code == 200
            data = response.json()
            assert len(data["reports"]) == 1
            assert data["reports"][0]["document_type"] == "LAB_REPORT"
            assert data["reports"][0]["category"] == "labs"
            assert (
                data["reports"][0]["source_display"]
                == "Imported by you from an external report"
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

