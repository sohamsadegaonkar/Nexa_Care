"""Tests for Workstream 2 Cryptographic Signed Approval endpoint.

Proves:
1. valid signed approval issues consent grant & doctor status sees approved state
2. patient does not receive doctor consent token in response
3. forged signature rejected (401)
4. wrong patient key / unenrolled device rejected (401)
5. revoked device key rejected (401)
6. expired challenge rejected (404)
7. replayed approval rejected (409)
8. denial with valid signature marks status denied and works (200)
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
from app.models.patient_device_keys import PatientDeviceKey

client = TestClient(app)


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


def test_valid_signed_approval_issues_grant_and_doctor_sees_approved(mock_scoped_pat, keypair_and_device):
    """Test 1 & 9 & 10: valid signed approval issues grant, returns 200 without token to patient, doctor sees approved."""
    private_key, dev_id, mock_dev = keypair_and_device
    req_id = str(uuid.uuid4())
    nonce = "nonce-abc"
    prov_id = "doc-202"

    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "provider_id": prov_id,
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration": 900,
        "challenge_nonce": nonce,
        "expires_at": "2099-07-07T16:05:00Z",
        "status": "pending",
    }

    sig_b64 = sign_payload(private_key, req_id, mock_scoped_pat, prov_id, nonce, "approved", "clinical", "routine_checkup", 900, "2099-07-07T16:05:00Z")

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_dev
    mock_res.scalars.return_value.all.return_value = [mock_dev]
    mock_res.scalars.return_value.all.return_value = [mock_dev]
    mock_db.execute.return_value = mock_res

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func, \
             patch("app.api.v2.consent_routes.consent_engine.issue", new_callable=AsyncMock, return_value="minted-token-xyz"), \
             patch("app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock):
            mock_redis = MagicMock()
            mock_redis.get.side_effect = lambda k: None if k.startswith("biometric_nonce:") else json.dumps(challenge_data)
            mock_redis_func.return_value = mock_redis

            payload = {
                "request_id": req_id,
                "patient_id": mock_scoped_pat,
                "decision": "approved",
                "challenge_nonce": nonce,
                "signature": sig_b64,
                "device_id": dev_id,
            }
            res = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=payload)
            assert res.status_code == 200, f"Approve signed failed: {res.text}"
            data = res.json()
            assert data["status"] == "approved"
            assert data["request_id"] == req_id
            assert "consent_token" not in data, "Patient received doctor consent token!"

            # Verify Redis set was updated with approved state and token for polling doctor
            assert mock_redis.set.call_count == 3
            consent_req_call = [call for call in mock_redis.set.call_args_list if call[0][0] == f"consent_request:{req_id}"][0]
            saved_json = json.loads(consent_req_call[0][1])
            assert saved_json["status"] == "approved"
            assert saved_json["consent_token"] == "minted-token-xyz"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_forged_signature_rejected(mock_scoped_pat, keypair_and_device):
    """Test 2: forged signature returns 401."""
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
        "expires_at": "2099-07-07T16:05:00Z",
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
            assert "Signature verification failed" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_wrong_patient_key_rejected(mock_scoped_pat):
    """Test 3: device key belonging to another patient / not enrolled returns 401."""
    req_id = str(uuid.uuid4())
    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "provider_id": "doc-1",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration": 900,
        "challenge_nonce": "nonce-1",
        "expires_at": "2099-07-07T16:05:00Z",
        "status": "pending",
    }

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None  # Key not found for this patient
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
                "device_id": str(uuid.uuid4()),
            }
            res = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=payload)
            assert res.status_code == 401
            assert "Device not enrolled" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_revoked_device_key_rejected(mock_scoped_pat, keypair_and_device):
    """Test 4: revoked hardware key returns 401."""
    _, dev_id, mock_dev = keypair_and_device
    mock_dev.revoked_at = datetime.now(timezone.utc)
    req_id = str(uuid.uuid4())
    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "provider_id": "doc-1",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration": 900,
        "challenge_nonce": "nonce-1",
        "expires_at": "2099-07-07T16:05:00Z",
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
            assert "Biometric binding revoked" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_expired_challenge_rejected(mock_scoped_pat):
    """Test 5: expired / missing Redis challenge returns 404."""
    req_id = str(uuid.uuid4())
    with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func:
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
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
        assert res.status_code == 404
        assert "Challenge expired or not found" in res.json()["detail"]


def test_replayed_approval_rejected(mock_scoped_pat):
    """Test 6: replayed approval on already resolved challenge returns 409."""
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
        assert "Request already resolved" in res.json()["detail"]


def test_denial_with_valid_signature_works(mock_scoped_pat, keypair_and_device):
    """Test 7: denial decision with valid signature updates status to denied."""
    private_key, dev_id, mock_dev = keypair_and_device
    req_id = str(uuid.uuid4())
    nonce = "nonce-den"
    prov_id = "doc-202"

    challenge_data = {
        "request_id": req_id,
        "patient_id": mock_scoped_pat,
        "provider_id": prov_id,
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration": 900,
        "challenge_nonce": nonce,
        "expires_at": "2099-07-07T16:05:00Z",
        "status": "pending",
    }

    sig_b64 = sign_payload(private_key, req_id, mock_scoped_pat, prov_id, nonce, "denied", "clinical", "routine_checkup", 900, "2099-07-07T16:05:00Z")

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_dev
    mock_res.scalars.return_value.all.return_value = [mock_dev]
    mock_db.execute.return_value = mock_res

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func, \
             patch("app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock):
            mock_redis = MagicMock()
            mock_redis.get.side_effect = lambda k: None if k.startswith("biometric_nonce:") else json.dumps(challenge_data)
            mock_redis_func.return_value = mock_redis

            payload = {
                "request_id": req_id,
                "patient_id": mock_scoped_pat,
                "decision": "denied",
                "challenge_nonce": nonce,
                "signature": sig_b64,
                "device_id": dev_id,
            }
            res = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=payload)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "denied"
            assert "consent_token" not in data

            saved_json = json.loads(mock_redis.set.call_args[0][1])
            assert saved_json["status"] == "denied"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
