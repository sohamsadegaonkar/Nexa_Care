"""Contract-conformance test suite for Nexa Care V2 Alpha Milestone.

Verifies route existence, mandatory dual-gating (Provider Auth + Zero-Trust Consent),
and exact response schema compliance against docs/API-CONTRACTS.md.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.core.dependencies import get_current_provider
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_override(request, admin_context):
    if "test_endpoints_require_auth" not in request.node.name:
        app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


def test_all_contract_routes_registered():
    """Verify that every endpoint documented in docs/API-CONTRACTS.md is registered in app.routes."""
    registered = {
        (m, r.path)
        for r in app.routes
        if hasattr(r, "methods")
        for m in r.methods
    }

    documented_endpoints = [
        ("POST", "/api/v2/patient/devices/enroll"),
        ("GET", "/api/v2/patient/devices"),
        ("POST", "/api/v2/consent/request"),
        ("POST", "/api/v2/consent/approve-signed"),
        ("GET", "/api/v2/consent/status/{request_id}"),
        ("GET", "/api/v2/patient/{id}/summary"),
        ("GET", "/api/v2/patient/{id}/timeline"),
        ("POST", "/api/v2/patient/{id}/record/vitals"),
        ("POST", "/api/v2/pipeline/documents/upload"),
        ("GET", "/api/v2/pipeline/jobs/{job_id}"),
        ("GET", "/api/v2/pipeline/review-queue"),
        ("POST", "/api/v2/pipeline/fields/{field_id}/review"),
        ("POST", "/api/v2/pipeline/jobs/{job_id}/commit"),
    ]

    for method, path in documented_endpoints:
        assert (method, path) in registered, f"Contract endpoint missing from app: {method} {path}"


@pytest.mark.parametrize(
    "method,path,is_patient_data",
    [
        ("POST", "/api/v2/patient/devices/enroll", True),
        ("GET", "/api/v2/patient/devices", True),
        ("POST", "/api/v2/consent/request", False),
        ("POST", "/api/v2/consent/approve-signed", False),
        ("GET", "/api/v2/consent/status/req-123", False),
        ("GET", "/api/v2/patient/pat-123/summary", True),
        ("GET", "/api/v2/patient/pat-123/timeline", True),
        ("POST", "/api/v2/patient/pat-123/record/vitals", True),
        ("POST", "/api/v2/pipeline/documents/upload", True),
        ("GET", "/api/v2/pipeline/jobs/job-123", True),
        ("GET", "/api/v2/pipeline/review-queue", True),
        ("POST", "/api/v2/pipeline/fields/field-123/review", True),
        ("POST", "/api/v2/pipeline/jobs/job-123/commit", True),
    ],
)
def test_endpoints_require_auth(method: str, path: str, is_patient_data: bool):
    """Verify that calling endpoints without Authorization bearer token returns 401 Unauthorized."""
    response = client.request(method, path, json={})
    assert response.status_code == 401, f"{method} {path} should return 401 unauthenticated, got {response.status_code}"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v2/patient/pat-123/summary"),
        ("GET", "/api/v2/patient/pat-123/timeline"),
        ("POST", "/api/v2/patient/pat-123/record/vitals"),
        ("POST", "/api/v2/pipeline/documents/upload"),
        ("GET", "/api/v2/pipeline/jobs/job-123"),
        ("GET", "/api/v2/pipeline/review-queue"),
        ("POST", "/api/v2/pipeline/fields/field-123/review"),
        ("POST", "/api/v2/pipeline/jobs/job-123/commit"),
    ],
)
def test_patient_data_endpoints_require_consent(admin_headers, method: str, path: str):
    """Verify that calling patient-data endpoints without valid X-Consent-Token returns 403 Forbidden."""
    with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
        response = client.request(method, path, headers=admin_headers, json={})
        assert response.status_code == 403, f"{method} {path} should return 403 when lacking consent token, got {response.status_code}"


def test_device_enrollment_response_schema():
    from app.core.dependencies import get_scoped_session
    from app.core.database import get_db_session
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import base64
    from unittest.mock import AsyncMock, MagicMock

    private_key = ec.generate_private_key(ec.SECP256R1())
    der_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    b64_key = base64.b64encode(der_bytes).decode("utf-8")

    mock_db = AsyncMock()
    mock_res_count = MagicMock()
    mock_res_count.scalar.return_value = 0
    mock_res_exist = MagicMock()
    mock_res_exist.scalar_one_or_none.return_value = None
    mock_db.execute.side_effect = [mock_res_count, mock_res_exist]

    app.dependency_overrides[get_scoped_session] = lambda: "123e4567-e89b-12d3-a456-426614174001"
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        payload = {
            "device_public_key": b64_key,
            "device_label": "Test iPhone",
            "platform": "ios",
            "expo_push_token": "ExponentPushToken[123]",
        }
        with patch("app.api.v2.device_routes.append_audit_log_or_503", new_callable=AsyncMock):
            res = client.post(
                "/api/v2/patient/devices/enroll",
                headers={"Authorization": "Bearer pat-tok"},
                json=payload,
            )
            assert res.status_code == 201
            data = res.json()
            assert "device_id" in data
            assert data["patient_id"] == "123e4567-e89b-12d3-a456-426614174001"
            assert data["status"] == "active"
            assert "enrolled_at" in data
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)
        app.dependency_overrides.pop(get_db_session, None)


def test_patient_summary_response_schema(admin_headers):
    mock_cap = ConsentCapability(
        patient_id="pat-123",
        clinician_id="doc-123",
        purpose="clinical_summary",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.get(
            "/api/v2/patient/pat-123/summary",
            headers={**admin_headers, "X-Consent-Token": "tok"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["patient_id"] == "pat-123"
        assert "pii" in data
        assert "clinical_summary" in data
        assert data["shard_scope"] == "clinical"


def test_extraction_job_status_response_schema(admin_headers):
    mock_cap = ConsentCapability(
        patient_id="pat-123",
        clinician_id="doc-123",
        purpose="pipeline_status",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.get(
            "/api/v2/pipeline/jobs/job-123?patient_id=pat-123",
            headers={**admin_headers, "X-Consent-Token": "tok"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["job_id"] == "job-123"
        assert "extracted_fields" in data
        assert isinstance(data["extracted_fields"], list)
        field = data["extracted_fields"][0]
        assert "field_id" in field
        assert "risk_level" in field
        assert "validation_result" in field
