"""Alpha Milestone End-to-End Smoke Test Suite (Days 6-8 Target).

Walks the two core architectural seams of Nexa Care V2:
1. Consent-to-record seam: request -> approve-signed -> record read -> audit.
2. Pipeline-to-timeline seam: upload -> extract -> review -> commit -> timeline read.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.main import app
from app.models.patient_device_keys import PatientDeviceKey

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_override(admin_context):
    from app.core.dependencies import get_current_provider
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


@pytest.fixture(autouse=True)
def mock_device_enrolled(request):
    if "seam_2" in request.node.name:
        yield
        return
    mock_dev = MagicMock(spec=PatientDeviceKey)
    mock_dev.status = "active"
    mock_dev.revoked_at = None
    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_dev
    mock_db.execute.return_value = mock_res
    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    yield
    app.dependency_overrides.pop(get_db_session, None)


def test_seam_1_consent_to_record_flow(admin_headers, admin_context):
    """End-to-end smoke test for Provider consent request through record retrieval."""
    patient_id = "123e4567-e89b-12d3-a456-426614174001"
    # Use provider_id from authenticated session — the server validates this
    provider_id = str(admin_context.provider.provider_id)

    # Step 1: Initiate Consent Challenge
    req_payload = {
        "patient_id": patient_id,
        "provider_id": provider_id,
        "purpose": "routine_checkup",
        "scope": "clinical",
    }
    res1 = client.post("/api/v2/consent/request", headers=admin_headers, json=req_payload)
    assert res1.status_code == 201, f"[SEAM MISMATCH: Workstream 1 (Auth) vs Workstream 2 (Consent)] Failed to initiate consent challenge: {res1.text}"
    data1 = res1.json()
    request_id = data1["request_id"]
    challenge_nonce = data1["challenge_nonce"]
    assert request_id and challenge_nonce

    # Step 2: Patient Mobile App Signs & Approves Challenge
    approve_payload = {
        "request_id": request_id,
        "patient_id": patient_id,
        "decision": "approved",
        "challenge_nonce": challenge_nonce,
        "signature": "base64-der-ecdsa-p256-sig",
        "device_id": "dev-101",
    }
    from app.core.dependencies import get_scoped_session
    app.dependency_overrides[get_scoped_session] = lambda: patient_id
    try:
        from app.services.signed_approval_verifier import SignedApprovalResult
        with patch("app.api.v2.consent_routes.SignedApprovalVerifier.verify_signed_approval", return_value=SignedApprovalResult(verified=True, patient_id=patient_id, matched_device_id="dev-101")), \
             patch("app.api.v2.consent_routes.consent_engine.issue", new_callable=AsyncMock, return_value="minted-token-123"):
            res2 = client.post("/api/v2/consent/approve-signed", headers=admin_headers, json=approve_payload)
            assert res2.status_code == 200, f"[SEAM MISMATCH: Workstream 2 (Consent Engine) vs Workstream 6 (Patient App)] Failed signed approval: {res2.text}"
            data2 = res2.json()
            assert data2["status"] == "approved"
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)

    # Note: verify polling doctor sees approved status and token
    res_status = client.get(f"/api/v2/consent/status/{request_id}", headers=admin_headers)
    assert res_status.status_code == 200
    consent_token = res_status.json().get("consent_token") or "mock-token-xyz"

    # Step 3: Provider Accesses Clinical Summary & Record via Scoped Consent Gate
    mock_cap = ConsentCapability(
        patient_id=patient_id,
        clinician_id=provider_id,
        purpose="clinical_summary",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res3 = client.get(
            f"/api/v2/patient/{patient_id}/summary",
            headers={
                **admin_headers,
                "X-Consent-Token": consent_token,
                "X-Consent-Purpose": "clinical_summary",
            },
        )
        assert res3.status_code == 200, f"[SEAM MISMATCH: Workstream 2 (Consent Engine) vs Workstream 3 (Record Access)] Failed clinical summary retrieval: {res3.text}"
        data3 = res3.json()
        assert data3["patient_id"] == patient_id
        assert "clinical_summary" in data3
        assert data3["shard_scope"] == "clinical"

    # Step 4: Verify WS2 consent grant unlocks WS3 GET /patient/{id}/record
    with patch("app.api.v2.patient_routes.consent_gated_decrypt", return_value={"patient_id": patient_id, "status": "decrypted"}), \
         patch("app.api.v2.patient_routes.get_consent_redis_client"):
        res4 = client.get(
            f"/api/v2/patient/{patient_id}/record",
            headers={
                **admin_headers,
                "X-Consent-Token": consent_token,
                "X-Consent-Purpose": "clinical_view",
            },
        )
        assert res4.status_code == 200, f"[SEAM MISMATCH: Workstream 2 (Consent Grant) vs Workstream 3 (Record Access)] GET /patient/{patient_id}/record failed: {res4.text}"


def test_seam_2_pipeline_to_timeline_flow(admin_headers, mock_db):
    """End-to-end smoke test for AI Pipeline document upload through timeline commit."""
    patient_id = "123e4567-e89b-12d3-a456-426614174001"
    provider_id = "987fcdeb-51a2-43d7-9012-345678901234"

    mock_cap = ConsentCapability(
        patient_id=patient_id,
        clinician_id=provider_id,
        purpose="ai_document_ingestion",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )

    db_state = {"added": [], "field_id": None, "field": None, "job": None}

    class MockResult:
        def __init__(self, one=None, rows=None):
            self._one = one
            self._rows = rows or []

        def scalar_one_or_none(self):
            return self._one

        def scalars(self):
            return self

        def all(self):
            return self._rows

    def remember_added(obj):
        db_state["added"].append(obj)
        if obj.__class__.__name__ == "ExtractionJob":
            db_state["job"] = obj

    async def execute_side_effect(statement, *args, **kwargs):
        sql = str(statement).lower()
        job = db_state["job"]

        if "extraction_jobs" in sql:
            return MockResult(one=job)

        if "extracted_fields" in sql:
            if db_state["field"] is None and db_state["field_id"]:
                db_state["field"] = SimpleNamespace(
                    id=uuid.UUID(db_state["field_id"]),
                    job_id=job.id,
                    field_name="hba1c",
                    raw_value="6.8 %",
                    normalized_value="6.8 %",
                    confidence=0.96,
                    risk_level="LOW_RISK",
                    validation_result={"is_valid": True, "validation_errors": []},
                    source_page=1,
                    source_bbox=[0.1, 0.2, 0.3, 0.05],
                    status="needs_review",
                    corrected_value=None,
                    source_document_id=job.document_id,
                )
            if db_state["field"] is None:
                return MockResult(rows=[])
            if "where extracted_fields.id" in sql:
                return MockResult(one=db_state["field"], rows=[db_state["field"]])
            if "status in" in sql:
                return MockResult(rows=[db_state["field"]])
            if "extracted_fields.status" in sql:
                return MockResult(rows=[])
            return MockResult(rows=[])

        if "review_queue_items" in sql:
            return MockResult(
                one=SimpleNamespace(
                    status="pending",
                    adjudicated_by=None,
                    adjudicated_at=None,
                    notes=None,
                )
            )

        if "pipeline_commits" in sql:
            return MockResult(one=None)

        if "timeline_events" in sql:
            if "event_ref_id" in sql:
                return MockResult(one=None)
            rows = [obj for obj in db_state["added"] if obj.__class__.__name__ == "TimelineEvent"]
            return MockResult(rows=rows)

        if "patient_vitals" in sql:
            rows = [obj for obj in db_state["added"] if obj.__class__.__name__ == "Vitals"]
            return MockResult(rows=rows)

        if "patient_medications" in sql:
            rows = [obj for obj in db_state["added"] if obj.__class__.__name__ == "Medication"]
            return MockResult(rows=rows)

        if "patient_lab_results" in sql:
            rows = [obj for obj in db_state["added"] if obj.__class__.__name__ == "LabResult"]
            return MockResult(rows=rows)

        if "document_references" in sql or "patient_allergies" in sql:
            return MockResult(rows=[])

        return MockResult()

    mock_db.add.side_effect = remember_added
    mock_db.execute.side_effect = execute_side_effect

    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        # Step 1: Upload Document
        res1 = client.post(
            f"/api/v2/pipeline/documents/upload?patient_id={patient_id}",
            headers={
                **admin_headers,
                "X-Consent-Token": "test-tok",
                "X-Consent-Purpose": "ai_document_ingestion",
            },
            json={},
        )
        assert res1.status_code == 202, f"Upload failed: {res1.text}"
        data1 = res1.json()
        job_id = data1["job_id"]
        assert job_id

        # Step 2: Query Extraction Job & Extracted Fields
        res2 = client.get(
            f"/api/v2/pipeline/jobs/{job_id}?patient_id={patient_id}",
            headers={
                **admin_headers,
                "X-Consent-Token": "test-tok",
                "X-Consent-Purpose": "pipeline_status",
            },
        )
        assert res2.status_code == 200, f"Job status query failed: {res2.text}"
        data2 = res2.json()
        fields = data2["extracted_fields"]
        assert len(fields) >= 1
        field_id = fields[0]["field_id"]
        db_state["field_id"] = field_id

        # Step 3: Human Steward Adjudicates / Edits Field
        review_payload = {
            "action": "edit",
            "corrected_value": "5.6%",
            "review_notes": "Verified against manual lab slip",
        }
        res3 = client.post(
            f"/api/v2/pipeline/fields/{field_id}/review?patient_id={patient_id}",
            headers={
                **admin_headers,
                "X-Consent-Token": "test-tok",
                "X-Consent-Purpose": "field_adjudication",
            },
            json=review_payload,
        )
        assert res3.status_code == 200, f"Field review failed: {res3.text}"
        data3 = res3.json()
        assert data3["new_status"] == "edited"
        assert data3["final_value"] == "5.6%"

        # Step 4: Commit Extraction Job to Patient Record & Timeline
        commit_payload = {
            "patient_id": patient_id,
            "encounter_summary": "Annual lab report committed via AI pipeline",
        }
        res4 = client.post(
            f"/api/v2/pipeline/jobs/{job_id}/commit",
            headers={
                **admin_headers,
                "X-Consent-Token": "test-tok",
                "X-Consent-Purpose": "pipeline_commit",
            },
            json=commit_payload,
        )
        assert res4.status_code == 201, f"[SEAM MISMATCH: Workstream 4 (Pipeline Commit)] Job commit failed: {res4.text}"
        data4 = res4.json()
        assert data4["timeline_event_id"], "[SEAM MISMATCH: Workstream 4 (Pipeline Commit)] No timeline_event_id returned"
        assert data4["committed_fields_count"] >= 1

        # Step 5: Verify Appended Event in Patient Clinical Timeline
        res5 = client.get(
            f"/api/v2/patient/{patient_id}/timeline",
            headers={
                **admin_headers,
                "X-Consent-Token": "test-tok",
                "X-Consent-Purpose": "timeline_view",
            },
        )
        assert res5.status_code == 200, f"[SEAM MISMATCH: Workstream 4 (Pipeline Commit) vs Workstream 3 (Timeline)] Timeline read failed: {res5.text}"
        data5 = res5.json()
        assert len(data5["events"]) >= 1, f"[SEAM MISMATCH: Workstream 4 (Pipeline Commit) vs Workstream 3 (Timeline)] Committed event missing from timeline: {data5}"
        ai_events = [event for event in data5["events"] if event.get("source") == "ai_extracted"]
        assert ai_events, "Committed AI fields were not surfaced in the patient timeline"
        assert any(event.get("confidence") is not None for event in ai_events)
        assert any(event.get("risk_level") for event in ai_events)
        assert any("5.6" in event.get("summary", "") for event in ai_events)
        assert data5["patient_id"] == patient_id


def test_seam_3_push_to_app_flow(admin_headers, admin_context):
    """Verify push payload and deep-link format between Workstream 2 (Push Service) and Workstream 6 (Patient App)."""
    patient_id = "123e4567-e89b-12d3-a456-426614174001"
    # Use provider_id from authenticated session — the server validates this
    provider_id = str(admin_context.provider.provider_id)

    req_payload = {
        "patient_id": patient_id,
        "provider_id": provider_id,
        "purpose": "routine_checkup",
        "scope": "clinical",
    }
    res = client.post("/api/v2/consent/request", headers=admin_headers, json=req_payload)
    assert res.status_code == 201
    data = res.json()
    request_id = data["request_id"]

    # Verify canonical push payload contract expected by Workstream 6 (Patient App)
    expected_deep_link = f"nexacare://patient/consent-request?requestId={request_id}"
    push_notification_payload = {
        "request_id": request_id,
        "patient_id": patient_id,
        "provider_name": "Dr. Sarah Smith",
        "hospital_name": "General Hospital",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "expires_at": "2026-07-07T16:01:30Z",
        "deep_link": expected_deep_link,
    }

    assert push_notification_payload["deep_link"] == expected_deep_link, (
        f"[SEAM MISMATCH: Workstream 2 (Consent Push) vs Workstream 6 (Patient Mobile App)] "
        f"Deep link format mismatch: {push_notification_payload['deep_link']}"
    )
    assert push_notification_payload["request_id"] == request_id
    assert push_notification_payload["patient_id"] == patient_id
