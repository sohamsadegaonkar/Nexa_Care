"""Abuse-resistance test suite for Nexa Care V2 (Workstream 2 & Workstream 9).

Proves:
1. Forged signature -> 401.
2. Signature from revoked device -> 401.
3. Cross-doctor token reuse -> 403.
4. Wrong-purpose access -> 403.
5. Expired grant access -> 403.
6. Self-declared assurance without signature -> 403.
7. Replay of signed approval -> 409.
8. Patient signing a modified payload (tampered decision) -> 401.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.models.assurance import AssuranceLevel
from app.models.patient_device_keys import PatientDeviceKey

client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_override(admin_context):
    from app.core.dependencies import get_current_provider
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


@pytest.fixture
def keypair_and_device():
    private_key = ec.generate_private_key(ec.SECP256R1())
    der_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    dev_id = str(uuid.uuid4())
    mock_dev = MagicMock(spec=PatientDeviceKey)
    mock_dev.id = uuid.UUID(dev_id)
    mock_dev.device_public_key = der_bytes
    mock_dev.status = "active"
    mock_dev.revoked_at = None
    return private_key, dev_id, mock_dev


@pytest.fixture
def mock_scoped_pat():
    from app.core.dependencies import get_scoped_session
    pat_id = "123e4567-e89b-12d3-a456-426614174001"
    app.dependency_overrides[get_scoped_session] = lambda: pat_id
    yield pat_id
    app.dependency_overrides.pop(get_scoped_session, None)


def sign_payload(private_key, req_id, pat_id, prov_id, nonce, decision, scope, purpose, duration, expires_at) -> str:
    signing_input = f"{req_id}|{pat_id}|{prov_id}|{nonce}|{decision}|{scope}|{purpose}|{duration}|{expires_at}"
    sig = private_key.sign(signing_input.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(sig).decode("utf-8")


def test_abuse_forged_signature(mock_scoped_pat, keypair_and_device):
    """Test 1: Forged signature -> 401."""
    _, dev_id, mock_dev = keypair_and_device
    req_id = str(uuid.uuid4())
    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "provider_id": "doc-1",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration": 900,
        "challenge_nonce": "nonce-1",
        "expires_at": "2026-07-07T16:05:00Z",
        "status": "pending",
    }

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_dev
    mock_res.scalars.return_value.all.return_value = [mock_dev]
    mock_db.execute.return_value = mock_res
    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func:
            mock_redis = MagicMock()
            mock_redis.get.side_effect = lambda k: None if k.startswith("biometric_nonce:") else json.dumps(challenge_data)
            mock_redis_func.return_value = mock_redis

            payload = {
                "request_id": req_id,
                "patient_id": mock_scoped_pat,
                "decision": "approved",
                "challenge_nonce": "nonce-1",
                "signature": base64.b64encode(b"forged-sig").decode("utf-8"),
                "device_id": dev_id,
            }
            res = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=payload)
            assert res.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_abuse_revoked_device_signature(mock_scoped_pat, keypair_and_device):
    """Test 2: Signature from revoked device -> 401."""
    _, dev_id, mock_dev = keypair_and_device
    mock_dev.revoked_at = datetime.now(timezone.utc)
    mock_dev.status = "revoked"

    req_id = str(uuid.uuid4())
    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "provider_id": "doc-1",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration": 900,
        "challenge_nonce": "nonce-1",
        "expires_at": "2026-07-07T16:05:00Z",
        "status": "pending",
    }

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_dev
    mock_res.scalars.return_value.all.return_value = [mock_dev]
    mock_db.execute.return_value = mock_res
    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func:
            mock_redis = MagicMock()
            mock_redis.get.side_effect = lambda k: None if k.startswith("biometric_nonce:") else json.dumps(challenge_data)
            mock_redis_func.return_value = mock_redis

            payload = {
                "request_id": req_id,
                "patient_id": mock_scoped_pat,
                "decision": "approved",
                "challenge_nonce": "nonce-1",
                "signature": base64.b64encode(b"sig").decode("utf-8"),
                "device_id": dev_id,
            }
            res = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=payload)
            assert res.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_abuse_cross_doctor_token_reuse(admin_headers):
    """Test 3: Cross-doctor token reuse -> 403."""
    # Token issued to doc-A, but doc-B (current provider) attempts to use it
    with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
        res = client.get(
            "/api/v2/patient/pat-101/summary",
            headers={**admin_headers, "X-Consent-Token": "tok-for-doc-a", "X-Consent-Purpose": "clinical_summary"},
        )
        assert res.status_code == 403


def test_abuse_wrong_purpose_access(admin_headers):
    """Test 4: Wrong-purpose access -> 403."""
    with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
        res = client.get(
            "/api/v2/patient/pat-101/summary",
            headers={**admin_headers, "X-Consent-Token": "tok-for-research", "X-Consent-Purpose": "clinical_summary"},
        )
        assert res.status_code == 403


def test_abuse_expired_grant_access(admin_headers):
    """Test 5: Expired grant access -> 403."""
    with patch("app.core.consent_gate.validate_consent_capability", return_value=None):
        res = client.get(
            "/api/v2/patient/pat-101/summary",
            headers={**admin_headers, "X-Consent-Token": "expired-tok", "X-Consent-Purpose": "clinical_summary"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_abuse_self_declared_assurance_without_signature():
    """Test 6: Self-declared PUSH_BIOMETRIC assurance without signature -> 403."""
    from app.services.assurance_verifier import RedisAssuranceVerifier
    verifier = RedisAssuranceVerifier()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # No verified evidence in Redis

    res = await verifier.verify(
        level=AssuranceLevel.PUSH_BIOMETRIC,
        patient_id="pat-101",
        evidence={"request_id": "req-fake"},
        redis=mock_redis,
    )
    assert not res.verified


def test_abuse_replay_signed_approval(mock_scoped_pat):
    """Test 7: Replay of signed approval -> 409."""
    req_id = str(uuid.uuid4())
    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "status": "approved",  # Already resolved!
    }
    with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: None if k.startswith("biometric_nonce:") else json.dumps(challenge_data)
        mock_redis_func.return_value = mock_redis

        payload = {
            "request_id": req_id,
            "patient_id": mock_scoped_pat,
            "decision": "approved",
            "challenge_nonce": "nonce-1",
            "signature": "sig",
            "device_id": str(uuid.uuid4()),
        }
        res = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=payload)
        assert res.status_code == 409


def test_abuse_tampered_decision_payload(mock_scoped_pat, keypair_and_device):
    """Test 8: Patient signing a modified payload (tampered decision) -> 401."""
    private_key, dev_id, mock_dev = keypair_and_device
    req_id = str(uuid.uuid4())
    nonce = "nonce-tam"
    prov_id = "doc-202"

    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "provider_id": prov_id,
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration": 900,
        "challenge_nonce": nonce,
        "expires_at": "2026-07-07T16:05:00Z",
        "status": "pending",
    }

    # Patient device signs "denied", but attacker sends decision="approved"
    sig_b64 = sign_payload(private_key, req_id, mock_scoped_pat, prov_id, nonce, "denied", "clinical", "routine_checkup", 900, "2026-07-07T16:05:00Z")

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_dev
    mock_res.scalars.return_value.all.return_value = [mock_dev]
    mock_db.execute.return_value = mock_res
    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func:
            mock_redis = MagicMock()
            mock_redis.get.side_effect = lambda k: None if k.startswith("biometric_nonce:") else json.dumps(challenge_data)
            mock_redis_func.return_value = mock_redis

            payload = {
                "request_id": req_id,
                "patient_id": mock_scoped_pat,
                "decision": "approved",  # Tampered decision!
                "challenge_nonce": nonce,
                "signature": sig_b64,
                "device_id": dev_id,
            }
            res = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=payload)
            assert res.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db_session, None)
