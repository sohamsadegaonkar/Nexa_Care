"""Pilot-blocking security invariants."""

from pathlib import Path

from app.api.v2.pipeline_routes import ALLOWED_COMMIT_STATUSES
from app.main import app
from app.models.pipeline import ExtractedFieldRecord

ROOT = Path(__file__).resolve().parents[1]


def test_patient_record_routes_are_consent_gated():
    code = (ROOT / "app/api/v2/patient_record_routes.py").read_text(encoding="utf-8")
    assert code.count('Depends(require_consent("') >= 8


def test_record_reads_are_hard_audited():
    code = (ROOT / "app/core/consent_gate.py").read_text(encoding="utf-8")
    assert "PATIENT_RECORD_READ_SUCCESS" in code
    assert "append_audit_log_or_503" in code


def test_extracted_fields_default_to_review_and_never_auto_commit():
    assert ExtractedFieldRecord.__table__.c.status.default.arg == "needs_review"
    assert ALLOWED_COMMIT_STATUSES == {"approved", "edited"}


def test_orchestrator_never_marks_extraction_auto_approved():
    code = (ROOT / "app/services/pipeline_orchestrator.py").read_text(encoding="utf-8")
    assert "auto_approved" not in code
    assert 'status="needs_review"' in code


def test_legacy_push_decision_route_is_absent():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/v2/push/{request_id}/respond" not in paths
    assert "/api/v2/consent/approve-signed" in paths


def test_signed_approval_binds_canonical_v2_payload():
    backend = (ROOT / "app/services/signed_approval_verifier.py").read_text(encoding="utf-8")
    frontend = (ROOT / "nexa-client/packages/app/services/deviceKeys.ts").read_text(encoding="utf-8")
    assert "nexa-consent-v2" in backend and "nexa-consent-v2" in frontend
    assert "sort_keys=True" in backend


def test_browser_provider_auth_has_no_javascript_token_storage():
    code = (ROOT / "nexa-client/packages/app/features/doctor/ProviderAuthContext.tsx").read_text(encoding="utf-8")
    assert "sessionStorage" not in code and "localStorage" not in code
    assert "providerWebSession" in code and "providerWebLogout" in code


def test_production_kms_is_not_local():
    code = (ROOT / "app/services/crypto_kms.py").read_text(encoding="utf-8")
    assert "AWSKMSProvider" in code
    assert "Local envelope encryption is forbidden" in code


def test_pipeline_storage_is_adapter_backed_and_encrypted():
    route = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
    storage = (ROOT / "app/services/document_storage.py").read_text(encoding="utf-8")
    assert "get_document_storage" in route
    assert "AESGCM" in storage and "SSEKMSKeyId" in storage


def test_no_transparency_mock_router_is_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/api/v2/transparency") for path in paths)
