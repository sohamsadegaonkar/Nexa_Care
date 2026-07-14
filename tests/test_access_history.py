"""Test suite for hardened Access-History and Timeline Provenance display (Days 9-11).

Verifies:
1. Access history reflects complete reads from the canonical audit ledger.
2. Break-glass accesses are visibly flagged (is_break_glass=True).
3. Timeline items distinguish AI vs. Manual provenance with confidence and risk badges.
4. Read attempts without active consent tokens are blocked with 403 (Invariant 1).
5. Every successful read produces an audit ledger entry (Invariant 2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.main import app
from app.models.patient_records import Vitals

client = TestClient(app)


class FakeScalarResult:
    def __init__(self, row):
        self._row = row
    def scalar_one_or_none(self):
        return self._row


@pytest.fixture(autouse=True)
def auth_override(request, admin_context):
    from app.core.dependencies import get_current_provider, get_provider_context
    if "without_auth" not in request.node.name:
        app.dependency_overrides[get_current_provider] = lambda: admin_context
        app.dependency_overrides[get_provider_context] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)
    app.dependency_overrides.pop(get_provider_context, None)


def test_access_history_reflects_reads():
    """Test 1: Access history endpoint accurately reflects routine read accesses."""
    pat_id = "pat-ah-1"
    sample_audit_row = {
        "record_hash": "hash-read-1",
        "actor_uid": "doc-smith",
        "event_type": "PATIENT_RECORD_READ_SUCCESS",
        "created_at": "2026-07-07T14:30:00Z",
        "status": "SUCCESS",
        "payload": {
            "metadata": {
                "provider_name": "Dr. John Smith",
                "hospital_name": "CityCare Hospital",
                "purpose": "routine_checkup",
                "scope": ["clinical", "pii"],
            }
        },
    }
    from app.core.dependencies import get_scoped_session
    app.dependency_overrides[get_scoped_session] = lambda: pat_id
    try:
        with patch(
            "app.api.v2.patient_record_routes.read_audit_events",
            new=AsyncMock(return_value=[sample_audit_row]),
        ):
            res = client.get("/api/v2/patient/me/access-history", headers={"Authorization": f"Bearer {pat_id}"})
            assert res.status_code == 200
            data = res.json()
            assert data["patient_id"] == pat_id
            history = data["access_history"]
            assert len(history) == 1
            item = history[0]
            assert item["accessed_by"] == "Dr. John Smith (CityCare Hospital)"
            assert item["purpose"] == "routine_checkup"
            assert item["data_categories"] == ["clinical", "pii"]
            assert item["is_break_glass"] is False
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)


def test_access_history_flags_break_glass():
    """Test 2: Access history visibly flags break-glass emergency read accesses."""
    pat_id = "pat-ah-1"
    sample_bg_row = {
        "record_hash": "hash-bg-1",
        "actor_uid": "doc-em",
        "event_type": "BREAK_GLASS_GRANT_SUCCESS",
        "created_at": "2026-07-07T15:00:00Z",
        "status": "SUCCESS",
        "payload": {
            "metadata": {
                "provider_name": "Dr. ER Specialist",
                "hospital_name": "Trauma Center",
                "purpose": "EMERGENCY",
                "is_break_glass": True,
                "scope": ["full"],
            }
        },
    }
    from app.core.dependencies import get_scoped_session
    app.dependency_overrides[get_scoped_session] = lambda: pat_id
    try:
        with patch(
            "app.api.v2.patient_record_routes.read_audit_events",
            new=AsyncMock(return_value=[sample_bg_row]),
        ):
            res = client.get("/api/v2/patient/me/access-history", headers={"Authorization": f"Bearer {pat_id}"})
            assert res.status_code == 200
            data = res.json()
            history = data["access_history"]
            assert len(history) == 1
            bg_item = history[0]
            assert bg_item["is_break_glass"] is True
            assert bg_item["flag"] == "BREAK_GLASS_ACCESS"
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)


@pytest.mark.asyncio
async def test_timeline_shows_ai_provenance():
    """Test 3: Timeline correctly displays AI vs Manual provenance along with confidence and risk badges."""
    pat_id = "pat-ah-1"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-202",
        purpose="timeline_view",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    ai_vital = Vitals(
        patient_id=uuid.uuid4(),
        type="BP",
        value="125/82",
        unit="mmHg",
        recorded_at=datetime.now(timezone.utc),
        source="ai_extracted",
        confidence=0.95,
        risk_level="LOW_RISK",
    )
    mock_db = MagicMock()
    # Simulate Vitals returning 1 AI vital, others returning []
    mock_db.execute = AsyncMock()
    mock_db.execute.side_effect = [
        MagicMock(scalars=lambda: MagicMock(all=lambda: [])),        # TimelineEvent
        MagicMock(scalars=lambda: MagicMock(all=lambda: [ai_vital])), # Vitals
        MagicMock(scalars=lambda: MagicMock(all=lambda: [])),        # Meds
        MagicMock(scalars=lambda: MagicMock(all=lambda: [])),        # Labs
        MagicMock(scalars=lambda: MagicMock(all=lambda: [])),        # Docs
    ]

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
            res = client.get(f"/api/v2/patient/{pat_id}/timeline", headers={"X-Consent-Token": "valid-tok"})
            assert res.status_code == 200
            events = res.json()["events"]
            assert len(events) == 1
            ev = events[0]
            assert ev["source"] == "ai_extracted"
            assert "AI-extracted from document, 95% confidence" in ev["provenance"]
            assert "AI Extracted (95%)" in ev["badges"]
            assert "Risk: LOW_RISK" in ev["badges"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_read_without_consent_blocked(admin_headers):
    """Test 4: Patient data read without active consent token is strictly blocked (403) (Invariant 1)."""
    pat_id = "pat-ah-1"
    with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
        res = client.get(f"/api/v2/patient/{pat_id}/summary", headers=admin_headers)
        assert res.status_code == 403
        assert "Active consent token required" in res.json()["detail"]


def test_read_produces_audit_entry(admin_headers):
    """Test 5: Every successful patient record read emits an audit ledger entry (Invariant 2)."""
    pat_id = "pat-ah-1"
    mock_cap = ConsentCapability(
        patient_id=pat_id,
        clinician_id="doc-202",
        purpose="clinical_summary",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
         patch("app.core.consent_gate.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:
        res = client.get(f"/api/v2/patient/{pat_id}/summary", headers={**admin_headers, "X-Consent-Token": "valid-tok"})
        assert res.status_code == 200
        event_types = [call.kwargs.get("event_type") for call in mock_audit.call_args_list]
        assert "PATIENT_RECORD_READ_SUCCESS" in event_types
        assert "CONSENT_GATED_DECRYPT_STARTED" in event_types


def test_admin_audit_trail_returns_full_ledger(admin_headers):
    """Test 6: Admin audit trail returns complete, unfiltered audit ledger events."""
    pat_id = "pat-ah-1"
    sample_rows = [
        {"record_hash": "h1", "actor_uid": "sys", "event_type": "EXTRACTED_DATA_INGESTED", "status": "SUCCESS"},
        {"record_hash": "h2", "actor_uid": "doc", "event_type": "PATIENT_RECORD_READ_SUCCESS", "status": "SUCCESS"},
    ]
    with patch(
        "app.api.v2.patient_record_routes.read_audit_events",
        new=AsyncMock(return_value=sample_rows),
    ):
        res = client.get(f"/api/v2/patient/{pat_id}/audit-trail", headers=admin_headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data["audit_trail"]) == 2
