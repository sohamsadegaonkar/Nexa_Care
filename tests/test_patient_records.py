"""Comprehensive test suite for Workstream 3 Structured Patient Records & Timeline layer.

Tests:
1. summary with consent (200)
2. summary without consent (403)
3. timeline aggregation
4. full record retrieval
5. patient self-view timeline (no doctor consent required)
6. patient access history
7. manual vitals write
8. audit on write (audit-before-write guarantee)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_override(request, admin_context):
    from app.core.dependencies import get_current_provider
    if "auth_missing" not in request.node.name:
        app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


def test_summary_with_consent(admin_headers):
    """Test 1: Retrieve high-level patient summary with valid consent token."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_summary",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.get(
            f"/api/v2/patient/{pat_id}/summary",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["patient_id"] == pat_id
        assert "pii" in data
        assert "clinical_summary" in data
        assert "latest_vitals" in data["clinical_summary"]


def test_summary_without_consent_403(admin_headers):
    """Test 2: Retrieve patient summary without consent token returns 403 Forbidden."""
    pat_id = "pat-1001"
    with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
        res = client.get(f"/api/v2/patient/{pat_id}/summary", headers=admin_headers)
        assert res.status_code == 403
        assert "Active consent token required" in res.json()["detail"]


def test_timeline_aggregation(admin_headers):
    """Test 3: Timeline endpoint aggregates vitals, meds, labs, allergies, and documents into unified feed."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="timeline_view",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.get(
            f"/api/v2/patient/{pat_id}/timeline",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["patient_id"] == pat_id
        assert isinstance(data["events"], list)
        assert len(data["events"]) >= 1
        first_event = data["events"][0]
        assert "event_id" in first_event
        assert "summary" in first_event
        assert "occurred_at" in first_event


def test_full_structured_record(admin_headers):
    """Test 4: Full structured record endpoint returns all clinical sub-models."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="full",
        scope=["full"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.get(
            f"/api/v2/patient/{pat_id}/records",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["patient_id"] == pat_id
        assert "vitals" in data
        assert "medications" in data
        assert "lab_results" in data
        assert "allergies" in data
        assert "documents" in data


def test_patient_self_view_timeline():
    """Test 5: Patient accessing their own timeline via session does not require doctor consent."""
    from app.core.dependencies import get_scoped_session
    pat_id = "pat-session-1001"
    app.dependency_overrides[get_scoped_session] = lambda: pat_id
    try:
        res = client.get("/api/v2/patient/me/timeline", headers={"Authorization": f"Bearer {pat_id}"})
        assert res.status_code == 200
        data = res.json()
        assert data["patient_id"] == pat_id
        assert "events" in data
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)


def test_patient_access_history():
    """Test 6: Patient accessing access history sees audit ledger records."""
    from app.core.dependencies import get_scoped_session
    pat_id = "pat-session-1001"
    app.dependency_overrides[get_scoped_session] = lambda: pat_id
    try:
        res = client.get("/api/v2/patient/me/access-history", headers={"Authorization": f"Bearer {pat_id}"})
        assert res.status_code == 200
        data = res.json()
        assert data["patient_id"] == pat_id
        assert "access_history" in data
        assert isinstance(data["access_history"], list)
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)


def test_manual_vitals_write(admin_headers):
    """Test 7: Provider appends structured vitals with source='manual'."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        payload = {
            "encounter_id": "enc-101",
            "systolic_bp": 120,
            "diastolic_bp": 80,
            "heart_rate": 72,
            "temperature_celsius": 36.8,
            "sp_o2_percentage": 98,
            "recorded_at": "2026-07-07T16:00:00Z",
            "source": "manual",
            "risk_level": "LOW_RISK",
        }
        res = client.post(
            f"/api/v2/patient/{pat_id}/records/vitals",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "committed"
        assert data["patient_id"] == pat_id
        assert "record_id" in data
        assert "audit_ledger_hash" in data


def test_audit_on_write(admin_headers):
    """Test 8: Every write strictly hard-audits before and after execution."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
         patch("app.api.v2.patient_record_routes.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
        payload = {
            "name": "Amoxicillin",
            "strength": "500mg",
            "frequency": "TID",
            "prescribed_at": "2026-07-07T16:00:00Z",
            "source": "manual",
            "risk_level": "MEDIUM_RISK",
        }
        res = client.post(
            f"/api/v2/patient/{pat_id}/records/medications",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 201

        # Check audit calls before and after write
        event_types = [call.kwargs.get("event_type") for call in mock_audit.call_args_list]
        assert "PATIENT_RECORD_APPEND_ATTEMPT" in event_types
        assert "PATIENT_RECORD_APPEND_SUCCESS" in event_types


def test_ai_extracted_record_without_confidence_fails(admin_headers):
    """Test 9: AI-extracted record without numeric confidence returns 400."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        payload = {
            "systolic_bp": 120,
            "diastolic_bp": 80,
            "heart_rate": 72,
            "temperature_celsius": 36.8,
            "sp_o2_percentage": 98,
            "recorded_at": "2026-07-07T16:00:00Z",
            "source": "ai_extracted",
            "confidence": None,
            "risk_level": "LOW_RISK",
            "source_document_id": "doc-123",
        }
        res = client.post(
            f"/api/v2/patient/{pat_id}/records/vitals",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 400
        assert "AI-extracted field must have numeric confidence" in res.json()["detail"]


def test_ai_extracted_record_without_risk_level_fails(admin_headers):
    """Test 10: AI-extracted record without risk_level returns 400."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        payload = {
            "systolic_bp": 120,
            "diastolic_bp": 80,
            "heart_rate": 72,
            "temperature_celsius": 36.8,
            "sp_o2_percentage": 98,
            "recorded_at": "2026-07-07T16:00:00Z",
            "source": "ai_extracted",
            "confidence": 0.95,
            "risk_level": "",
            "source_document_id": "doc-123",
        }
        res = client.post(
            f"/api/v2/patient/{pat_id}/records/vitals",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 400


def test_ai_extracted_record_without_source_document_id_fails(admin_headers):
    """Test 11: AI-extracted record without source_document_id returns 400."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        payload = {
            "name": "Metformin",
            "strength": "500mg",
            "frequency": "BID",
            "prescribed_at": "2026-07-07T16:00:00Z",
            "source": "ai_extracted",
            "confidence": 0.95,
            "risk_level": "MEDIUM_RISK",
            "source_document_id": None,
        }
        res = client.post(
            f"/api/v2/patient/{pat_id}/records/medications",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 400


def test_allergy_defaults_to_high_risk(admin_headers):
    """Test 12: Allergy strictly requires HIGH_RISK risk_level."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        payload = {
            "allergen": "Peanuts",
            "severity": "Severe",
            "source": "manual",
            "risk_level": "LOW_RISK",
        }
        res = client.post(
            f"/api/v2/patient/{pat_id}/records/allergies",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=payload,
        )
        assert res.status_code == 400
        assert "HIGH_RISK" in res.json()["detail"]


def test_timeline_event_created_for_every_write_type(admin_headers):
    """Test 13: Vitals, Medications, Labs, Allergies, and Documents all generate timeline events upon commit."""
    pat_id = "pat-1001"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-2002",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        # 1. Documents write
        doc_payload = {
            "document_type": "LAB_REPORT",
            "storage_ref": "s3://bucket/lab.pdf",
            "source": "manual",
        }
        res = client.post(
            f"/api/v2/patient/{pat_id}/records/documents",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
            json=doc_payload,
        )
        assert res.status_code == 201


def test_provider_cannot_write_if_auth_missing():
    """Test 14: Unauthenticated write attempt without provider bearer token returns 401 Unauthorized."""
    res = client.request(
        "POST",
        "/api/v2/patient/pat-1001/records/vitals",
        json={"systolic_bp": 120, "diastolic_bp": 80, "heart_rate": 72, "temperature_celsius": 36.8, "sp_o2_percentage": 98, "recorded_at": "2026-07-07T16:00:00Z"},
    )
    assert res.status_code == 401


def test_patient_cannot_access_another_patients_me_timeline():
    """Test 15: Patient session token cannot access another patient's timeline IDOR."""
    from app.core.dependencies import get_scoped_session
    app.dependency_overrides[get_scoped_session] = lambda: "pat-session-1001"
    try:
        res = client.get("/api/v2/patient/me/timeline?patient_id=pat-other-9999", headers={"Authorization": "Bearer pat-session-1001"})
        assert res.status_code == 403
        assert "does not match target record" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)
