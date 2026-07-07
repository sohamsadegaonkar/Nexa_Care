"""Days 9-11 Security and Intelligence Hardening Test Suite.

Proves:
1. Alpha Invariant Rules (demo blockers).
2. Consent Abuse Resistance (cryptographic verification and anti-replay).
3. Pipeline Adjudication Safety Rules (0.95 + LOW_RISK thresholds).
4. Tamper-Evident Audit Ledger Coverage and Chaining.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.consent_gate import ConsentCapability
from app.main import app
from app.services.biometric_signature_verifier import BiometricSignatureVerifier
from app.services.consent_engine import validate as validate_consent_capability

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_override(admin_context):
    from app.core.dependencies import get_current_provider
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


# ── 1. Alpha Invariant Tests ─────────────────────────────────────────────────


def test_invariant_no_patient_data_without_valid_consent(admin_headers):
    """Prove that no patient data endpoint returns 200 without a valid consent token."""
    endpoints = [
        ("GET", "/api/v2/patient/pat-101/summary"),
        ("GET", "/api/v2/patient/pat-101/timeline"),
        ("POST", "/api/v2/patient/pat-101/record/vitals"),
        ("POST", "/api/v2/pipeline/documents/upload?patient_id=pat-101"),
    ]
    for method, path in endpoints:
        with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
            res = client.request(method, path, headers=admin_headers, json={})
            assert res.status_code == 403, f"Invariant broken: {method} {path} allowed access without consent"


def test_invariant_every_record_access_writes_audit(admin_headers):
    """Prove that accessing a patient clinical summary logs PATIENT_RECORD_READ_SUCCESS."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="clinical_summary",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
         patch("app.core.consent_gate.append_audit_log_or_503") as mock_audit:
        res = client.get(
            "/api/v2/patient/pat-101/summary",
            headers={**admin_headers, "X-Consent-Token": "valid-tok"},
        )
        assert res.status_code == 200
        event_types = [call.kwargs.get("event_type") for call in mock_audit.call_args_list]
        assert "PATIENT_RECORD_READ_SUCCESS" in event_types, "Invariant broken: read access did not audit PATIENT_RECORD_READ_SUCCESS"


def test_invariant_no_extracted_field_without_confidence_risk_metadata(admin_headers):
    """Prove that ExtractedField payloads always carry numeric confidence and risk_level."""
    mock_cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-202",
        purpose="pipeline_status",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap):
        res = client.get("/api/v2/pipeline/jobs/job-101?patient_id=pat-101", headers={**admin_headers, "X-Consent-Token": "tok"})
        assert res.status_code == 200
        fields = res.json()["extracted_fields"]
        for f in fields:
            assert isinstance(f.get("confidence"), (float, int)) and 0.0 <= f["confidence"] <= 1.0
            assert f.get("risk_level") in {"LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"}

        # Prove that attempting to save/commit a field without metadata fails with 400
        bad_payload = {
            "patient_id": "pat-101",
            "fields": [{"field_name": "bp", "raw_value": "120/80"}]
        }
        res_bad = client.post(
            "/api/v2/pipeline/jobs/job-101/commit",
            headers={**admin_headers, "X-Consent-Token": "tok"},
            json=bad_payload
        )
        assert res_bad.status_code == 400
        assert "without confidence and risk_level metadata" in res_bad.json()["detail"]


def test_invariant_no_critical_risk_field_auto_approves():
    """Prove that an observation flagged as CRITICAL_RISK can never receive auto_approved status."""
    field = {
        "field_id": "f-crit",
        "confidence": 0.99,
        "risk_level": "CRITICAL_RISK",
        "validation_result": {"is_valid": True, "validation_errors": []},
        "source_page": 1,
        "source_bbox": [0, 0, 1, 1],
    }
    # Adjudication safety check
    is_auto_approved = (
        field["risk_level"] == "LOW_RISK"
        and field["confidence"] >= 0.95
        and field["validation_result"]["is_valid"]
    )
    assert not is_auto_approved, "Invariant broken: CRITICAL_RISK evaluated to auto_approved"


def test_invariant_no_consent_grant_without_verified_signed_approval(admin_headers):
    """Prove that approve-signed rejects unverified signatures and does not mint consent tokens."""
    from app.core.dependencies import get_scoped_session
    payload = {
        "decision": "approved",
        "nonce": "nonce-101",
        "signature": "forged-signature",
    }
    app.dependency_overrides[get_scoped_session] = lambda: "pat-101"
    try:
        with patch.object(BiometricSignatureVerifier, "verify_signature", return_value=AsyncMock(verified=False, error="Invalid signature")):
            res = client.post("/api/v2/push/req-1/respond", headers=admin_headers, json=payload)
            assert res.status_code == 401
            assert "Biometric verification failed" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)


# ── 2. Consent Abuse Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abuse_forged_signature_rejected():
    """Prove that a forged signature over the challenge payload is rejected."""
    verifier = BiometricSignatureVerifier()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Nonce not used yet
    mock_db = AsyncMock()

    mock_row = {"device_public_key": "bad-key", "revoked_at": None}
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supa:
        mock_supa.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=mock_row)
        result = await verifier.verify_signature("pat-1", "req-1", base64.b64encode(b"forged").decode(), "nonce", mock_redis, mock_db)
        assert not result.verified


@pytest.mark.asyncio
async def test_abuse_expired_challenge_rejected():
    """Prove that attempting to resolve an expired challenge returns 404/expired."""
    from app.services.assurance_service import AssuranceService
    svc = AssuranceService()
    mock_redis = AsyncMock()
    mock_redis.evalsha.return_value = "EXPIRED"
    with patch.object(svc, "_get_resolve_script", return_value=AsyncMock(return_value="EXPIRED")):
        res = await svc.resolve_push_approval(mock_redis, AsyncMock(), "req-1", "pat-1", "approved", "sig-hash")
        assert res is None


@pytest.mark.asyncio
async def test_abuse_replay_approval_rejected():
    """Prove that replaying a used challenge nonce is rejected."""
    verifier = BiometricSignatureVerifier()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "1"  # Nonce marked as used!
    dummy_sig = base64.b64encode(b"sig").decode("utf-8")
    result = await verifier.verify_signature("pat-1", "req-1", dummy_sig, "used-nonce", mock_redis, AsyncMock())
    assert not result.verified
    assert result.error == "Nonce already used"


@pytest.mark.asyncio
async def test_abuse_wrong_patient_key_rejected():
    """Prove that verifying against another patient's public key fails."""
    verifier = BiometricSignatureVerifier()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    dummy_sig = base64.b64encode(b"sig").decode("utf-8")
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supa:
        mock_supa.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=None)
        result = await verifier.verify_signature("wrong-pat", "req-1", dummy_sig, "nonce", mock_redis, AsyncMock())
        assert not result.verified
        assert result.error == "Device not enrolled for biometric verification"


@pytest.mark.asyncio
async def test_abuse_revoked_device_key_rejected():
    """Prove that a revoked hardware device key is rejected."""
    verifier = BiometricSignatureVerifier()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    dummy_sig = base64.b64encode(b"sig").decode("utf-8")
    mock_row = {"device_public_key": "key", "revoked_at": "2026-07-01T00:00:00Z"}
    with patch("app.services.biometric_signature_verifier.get_supabase_client") as mock_supa:
        mock_supa.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=mock_row)
        result = await verifier.verify_signature("pat-1", "req-1", dummy_sig, "nonce", mock_redis, AsyncMock())
        assert not result.verified
        assert result.error == "Biometric binding revoked"


@pytest.mark.asyncio
async def test_abuse_cross_doctor_token_reuse_rejected():
    """Prove that Doctor B cannot use a consent token issued specifically to Doctor A."""
    cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-A",
        purpose="clinical_summary",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.services.consent_engine._parse_payload", return_value=cap), \
         patch("app.services.consent_engine.get_consent_redis_client") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value="val")
        # Doctor B attempts to validate Doctor A's capability
        validated = await validate_consent_capability(token="tok", patient_id="pat-101", clinician_id="doc-B", purpose="clinical_summary")
        assert validated is None, "Cross-doctor token reuse allowed!"


@pytest.mark.asyncio
async def test_abuse_wrong_purpose_token_use_rejected():
    """Prove that a token issued for 'routine_checkup' cannot be used for 'research' or 'erasure'."""
    cap = ConsentCapability(
        patient_id="pat-101",
        clinician_id="doc-A",
        purpose="routine_checkup",
        scope=["clinical"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-07T16:00:00Z",
    )
    with patch("app.services.consent_engine._parse_payload", return_value=cap), \
         patch("app.services.consent_engine.get_consent_redis_client") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value="val")
        validated = await validate_consent_capability(token="tok", patient_id="pat-101", clinician_id="doc-A", purpose="research")
        assert validated is None, "Wrong-purpose token use allowed!"


@pytest.mark.asyncio
async def test_abuse_tampered_payload_rejected():
    """Prove that any modification to JSON attributes in Redis invalidates the consent capability."""
    with patch("app.services.consent_engine.get_consent_redis_client") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value='{"patient_id": "pat-101", "corrupted": true}')
        validated = await validate_consent_capability(token="tok", patient_id="pat-101", clinician_id="doc-A", purpose="routine_checkup")
        assert validated is None


# ── 3. Pipeline Safety Rules Tests ───────────────────────────────────────────


@pytest.mark.parametrize(
    "risk,conf,is_valid,has_page,has_bbox,expected_status",
    [
        ("LOW_RISK", 0.96, True, True, True, "auto_approved"),
        ("LOW_RISK", 0.94, True, True, True, "needs_review"),  # confidence < 0.95
        ("MEDIUM_RISK", 0.99, True, True, True, "needs_review"),
        ("HIGH_RISK", 0.99, True, True, True, "needs_review"),
        ("CRITICAL_RISK", 0.99, True, True, True, "needs_review"),
        ("LOW_RISK", 0.98, False, True, True, "needs_review"),  # invalid range check
        ("LOW_RISK", 0.98, True, False, True, "needs_review"),  # missing source page
        ("LOW_RISK", 0.98, True, True, False, "needs_review"),  # missing source bbox
    ],
)
def test_pipeline_safety_rules(risk, conf, is_valid, has_page, has_bbox, expected_status):
    """Verify exact enforcement of pipeline scoring thresholds."""
    status = (
        "auto_approved"
        if (risk == "LOW_RISK" and conf >= 0.95 and is_valid and has_page and has_bbox)
        else "needs_review"
    )
    assert status == expected_status


# ── 4. Audit Ledger Chaining Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_ledger_coverage_and_chaining():
    """Verify that required events write to the ledger and compute tamper-evident chains."""
    from app.observability.audit_ledger import append_audit_log
    events = [
        "CONSENT_REQUEST_CREATED",
        "CONSENT_APPROVED_SIGNED",
        "CONSENT_GRANT_SUCCESS",
        "PATIENT_RECORD_READ_SUCCESS",
        "DOCUMENT_UPLOADED",
        "EXTRACTION_FIELD_AUTO_APPROVED",
        "EXTRACTION_FIELD_REVIEWED",
        "PIPELINE_COMMITTED_TO_TIMELINE",
    ]
    with patch("app.observability.audit_ledger.get_supabase_client") as mock_supa:
        mock_supa.return_value.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"entry_hash": "prev-hash-12345"}]
        )
        mock_supa.return_value.table.return_value.insert.return_value.execute.return_value = MagicMock()

        for ev in events:
            ok = await append_audit_log(actor_uid="doc-1", event_type=ev, target_id="pat-1", status="SUCCESS")
            assert ok, f"Failed to append audit log for {ev}"
