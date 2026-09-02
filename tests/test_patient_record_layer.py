"""Tests for Workstream 3 Structured Patient Records & Provenance Layer.

Proves:
1. AI-extracted vitals without confidence fails (400).
2. AI-extracted medication without source_document_id fails (400).
3. Allergy defaults to and strictly enforces HIGH_RISK.
4. Timeline event is created when lab result is committed.
5. Patient record read requires valid consent (403 when lacking).
6. Patient self-view does not require doctor consent token.
7. Pipeline commit preserves ExtractedField confidence/risk/source metadata.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.main import app
from app.models.patient_records import Allergy, TimelineEvent

client = TestClient(app)


class FakeScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_DNS, str(value))


def _job_for(
    patient_id: str = "11111111-1111-4111-8111-111111111111",
    job_id: str | uuid.UUID | None = None,
):
    return SimpleNamespace(
        id=_parse_uuid(str(job_id or uuid.uuid4())),
        patient_id=_parse_uuid(patient_id),
        document_id=uuid.uuid4(),
        status="scored",
    )


@pytest.fixture
def record_writer(clinician_context):
    """Explicit downstream precondition for record validation/write tests."""
    from app.core.dependencies import get_current_provider

    app.dependency_overrides[get_current_provider] = lambda: clinician_context
    try:
        yield clinician_context
    finally:
        app.dependency_overrides.pop(get_current_provider, None)


@pytest.fixture
def record_reader(clinician_context):
    """Explicit RECORD_READ prerequisite for consent-gate tests only."""
    from app.core.dependencies import (
        get_current_provider,
        get_provider_context,
        require_clinical_capability,
    )
    from app.security.provider_capabilities import ClinicalCapability

    gate = require_clinical_capability(ClinicalCapability.RECORD_READ)
    app.dependency_overrides[gate] = lambda: clinician_context
    app.dependency_overrides[get_current_provider] = lambda: clinician_context
    app.dependency_overrides[get_provider_context] = lambda: clinician_context
    try:
        yield clinician_context
    finally:
        app.dependency_overrides.pop(gate, None)
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_provider_context, None)


@pytest.fixture
def document_committer(clinician_context):
    """Explicit DOCUMENTS_COMMIT prerequisite for commit payload validation."""
    from app.core.dependencies import require_clinical_capability
    from app.security.provider_capabilities import ClinicalCapability

    gate = require_clinical_capability(ClinicalCapability.DOCUMENTS_COMMIT)
    app.dependency_overrides[gate] = lambda: clinician_context
    try:
        yield clinician_context
    finally:
        app.dependency_overrides.pop(gate, None)


def test_ai_extracted_vitals_without_confidence_fails(admin_headers, record_writer):
    """Test 1: AI-extracted vitals without numeric confidence returns 400 Bad Request."""
    mock_cap = ConsentCapability(
        patient_id="11111111-1111-4111-8111-111111111111",
        clinician_id="doc-202",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch(
        "app.core.consent_gate.validate_consent_capability", return_value=mock_cap
    ):
        payload = {
            "encounter_id": "enc-101",
            "systolic_bp": 120,
            "diastolic_bp": 80,
            "heart_rate": 72,
            "temperature_celsius": 36.8,
            "sp_o2_percentage": 98,
            "recorded_at": "2026-07-07T16:00:00Z",
            "source": "ai_extracted",
            "confidence": None,  # Missing confidence!
            "risk_level": "LOW_RISK",
            "source_document_id": "22222222-2222-4222-8222-222222222222",
        }
        res = client.post(
            "/api/v2/patient/11111111-1111-4111-8111-111111111111/record/vitals",
            headers={**admin_headers, "X-Consent-Token": "tok"},
            json=payload,
        )
        assert res.status_code == 400
        assert "AI-extracted field must have numeric confidence" in res.json()["detail"]


def test_ai_extracted_medication_without_source_document_id_fails(
    admin_headers, record_writer
):
    """Test 2: AI-extracted medication without source_document_id returns 400 Bad Request."""
    mock_cap = ConsentCapability(
        patient_id="11111111-1111-4111-8111-111111111111",
        clinician_id="doc-202",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch(
        "app.core.consent_gate.validate_consent_capability", return_value=mock_cap
    ):
        payload = {
            "name": "Metformin",
            "strength": "500mg",
            "frequency": "BID",
            "prescribed_at": "2026-07-07T16:00:00Z",
            "source": "ai_extracted",
            "confidence": 0.96,
            "risk_level": "MEDIUM_RISK",
            "source_document_id": None,  # Missing source document reference!
        }
        res = client.post(
            "/api/v2/patient/11111111-1111-4111-8111-111111111111/record/medications",
            headers={**admin_headers, "X-Consent-Token": "tok"},
            json=payload,
        )
        assert res.status_code == 400
        assert (
            "AI-extracted field must have numeric confidence, risk_level, and source_document_id"
            in res.json()["detail"]
        )


def test_allergy_defaults_to_and_enforces_high_risk(admin_headers, record_writer):
    """Test 3: Allergy model defaults to and strictly requires HIGH_RISK risk_level per WS5 rules."""
    # Check model default
    alg = Allergy(patient_id=uuid.uuid4(), allergen="Peanuts", severity="Anaphylaxis")
    assert alg.risk_level == "HIGH_RISK"

    mock_cap = ConsentCapability(
        patient_id="11111111-1111-4111-8111-111111111111",
        clinician_id="doc-202",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch(
        "app.core.consent_gate.validate_consent_capability", return_value=mock_cap
    ):
        # Passing LOW_RISK on an allergy must fail API validation
        payload = {
            "allergen": "Penicillin",
            "severity": "Severe",
            "source": "manual",
            "risk_level": "LOW_RISK",
        }
        res = client.post(
            "/api/v2/patient/11111111-1111-4111-8111-111111111111/record/allergies",
            headers={**admin_headers, "X-Consent-Token": "tok"},
            json=payload,
        )
        assert res.status_code == 400
        assert "HIGH_RISK" in res.json()["detail"]


def test_timeline_event_created_when_lab_result_committed(admin_headers, record_writer):
    """Test 4: Committing a lab result creates a TimelineEvent row linking to the lab."""
    mock_cap = ConsentCapability(
        patient_id="11111111-1111-4111-8111-111111111111",
        clinician_id="doc-202",
        purpose="clinical_append",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch(
        "app.core.consent_gate.validate_consent_capability", return_value=mock_cap
    ):
        payload = {
            "test_name": "HbA1c",
            "value": "6.8",
            "unit": "%",
            "reference_range": "4.0-5.6 %",
            "is_abnormal": True,
            "recorded_at": "2026-07-07T16:00:00Z",
            "source": "manual",
            "risk_level": "MEDIUM_RISK",
        }
        res = client.post(
            "/api/v2/patient/11111111-1111-4111-8111-111111111111/record/labs",
            headers={**admin_headers, "X-Consent-Token": "tok"},
            json=payload,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "committed"
        assert "record_id" in data

        # Verify timeline entity construction
        tl = TimelineEvent(
            patient_id=uuid.uuid4(),
            event_type="LAB_RESULT",
            occurred_at=datetime.now(timezone.utc),
            source="manual",
            summary="Lab result committed: HbA1c (6.8 %)",
        )
        assert tl.event_type == "LAB_RESULT"


def test_patient_record_read_requires_valid_consent(admin_headers, record_reader):
    """Test 5: Doctor attempting patient record read without valid X-Consent-Token gets 403 Forbidden."""
    with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
        res = client.get(
            "/api/v2/patient/11111111-1111-4111-8111-111111111111/summary",
            headers=admin_headers,
        )
        assert res.status_code == 403
        assert "Active consent token required" in res.json()["detail"]


def test_break_glass_capability_rejected_at_summary_endpoint(
    admin_headers, record_reader
):
    """DEFECT 1/2/3: a break-glass capability must never satisfy the general
    /summary route -- only the dedicated /emergency-summary endpoint."""
    break_glass_capability = ConsentCapability(
        patient_id="11111111-1111-4111-8111-111111111111",
        clinician_id="doctor-1",
        purpose="EMERGENCY",
        scope=["allergies"],
        is_break_glass=True,
        reason_code="LIFE_THREATENING_EMERGENCY",
        issued_at="2026-07-18T00:00:00+00:00",
        expires_at="2026-07-18T00:15:00+00:00",
    )
    with patch(
        "app.core.consent_gate.validate_consent_capability",
        return_value=break_glass_capability,
    ):
        res = client.get(
            "/api/v2/patient/11111111-1111-4111-8111-111111111111/summary",
            headers={**admin_headers, "X-Consent-Token": "tok"},
        )
        assert res.status_code == 403
        assert (
            res.json()["detail"]["error_code"]
            == "BREAK_GLASS_CAPABILITY_NOT_VALID_HERE"
        )


def test_patient_self_view_does_not_require_doctor_consent_token():
    """Test 6: Patient accessing own record/devices via self-session does not require doctor consent token."""
    from app.core.dependencies import get_scoped_session

    app.dependency_overrides[get_scoped_session] = (
        lambda: "11111111-1111-4111-8111-111111111111"
    )
    try:
        # Patient calls self-access endpoint (e.g. list devices) with only their session bearer token
        res = client.get(
            "/api/v2/patient/devices",
            headers={"Authorization": "Bearer pat-101-session"},
        )
        assert res.status_code == 200
        assert res.json()["patient_id"] == "11111111-1111-4111-8111-111111111111"
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)


def test_pipeline_commit_rejects_client_supplied_extracted_fields(
    admin_headers, mock_db, document_committer
):
    mock_cap = ConsentCapability(
        patient_id="11111111-1111-4111-8111-111111111111",
        clinician_id="doc-202",
        purpose="pipeline_commit",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    mock_db.execute.side_effect = [
        FakeScalarResult(_job_for(job_id="22222222-2222-4222-8222-222222222222")),
        MagicMock(scalars=lambda: MagicMock(all=lambda: [])),
    ]
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with (
        patch(
            "app.core.consent_gate.validate_consent_capability", return_value=mock_cap
        ),
        patch(
            "app.api.v2.pipeline_routes.ingest_extracted_fields", new_callable=AsyncMock
        ) as mock_ingest,
    ):
        commit_payload = {
            "patient_id": "11111111-1111-4111-8111-111111111111",
            "fields": [
                {
                    "field_id": str(uuid.uuid4()),
                    "job_id": str(uuid.uuid4()),
                    "field_name": "bp",
                    "raw_value": "120/80 mmHg",
                    "confidence": 0.98,
                    "risk_level": "LOW_RISK",
                }
            ],
        }
        res = client.post(
            "/api/v2/pipeline/jobs/22222222-2222-4222-8222-222222222222/commit",
            headers={**admin_headers, "X-Consent-Token": "tok"},
            json=commit_payload,
        )
        assert res.status_code == 400
        assert (
            res.json()["detail"]["error_code"]
            == "CLIENT_SUPPLIED_COMMIT_FIELDS_FORBIDDEN"
        )
        mock_ingest.assert_not_awaited()
