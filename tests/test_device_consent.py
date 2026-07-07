"""Tests for Workstream 2 Device Enrollment and Consent Request endpoints.

Verifies:
1. enroll device (201)
2. list devices (200, never raw keys)
3. request consent with no enrolled device (409)
4. request consent creates pending challenge (201)
5. consent status returns pending (200)
6. consent status returns expired when Redis key is gone (200)
"""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.models.patient_device_keys import PatientDeviceKey

client = TestClient(app)


@pytest.fixture
def sample_p256_der_b64() -> str:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    der_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der_bytes).decode("utf-8")


@pytest.fixture
def mock_scoped_session():
    from app.core.dependencies import get_scoped_session
    pat_id = "123e4567-e89b-12d3-a456-426614174001"
    app.dependency_overrides[get_scoped_session] = lambda: pat_id
    yield pat_id
    app.dependency_overrides.pop(get_scoped_session, None)


@pytest.fixture
def mock_provider_auth(admin_context):
    from app.core.dependencies import get_current_provider
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield admin_context
    app.dependency_overrides.pop(get_current_provider, None)


def test_enroll_device(mock_scoped_session, sample_p256_der_b64):
    """Test 1: enroll device validates P-256 DER key, stores active, returns device_id."""
    payload = {
        "device_public_key": sample_p256_der_b64,
        "device_label": "iPhone 15 Pro Max",
        "platform": "ios",
    }
    mock_db = AsyncMock()
    mock_res_count = MagicMock()
    mock_res_count.scalar.return_value = 0
    mock_res_exist = MagicMock()
    mock_res_exist.scalar_one_or_none.return_value = None
    mock_db.execute.side_effect = [mock_res_count, mock_res_exist]

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        with patch("app.api.v2.device_routes.append_audit_log_or_503", new_callable=AsyncMock):
            res = client.post(
                "/api/v2/patient/devices/enroll",
                headers={"Authorization": "Bearer pat-tok"},
                json=payload,
            )
            assert res.status_code == 201, f"Enroll device failed: {res.text}"
            data = res.json()
            assert data["status"] == "active"
            assert "device_id" in data
            assert data["patient_id"] == mock_scoped_session
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_list_devices(mock_scoped_session):
    """Test 2: list devices returns enrolled device metadata without exposing raw public keys."""
    mock_db = AsyncMock()
    mock_dev = MagicMock(spec=PatientDeviceKey)
    mock_dev.id = uuid.uuid4()
    mock_dev.device_label = "iPhone 14"
    mock_dev.platform = "ios"
    mock_dev.status = "active"
    from datetime import datetime, timezone
    mock_dev.enrolled_at = datetime.now(timezone.utc)

    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [mock_dev]
    mock_db.execute.return_value = mock_res

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db
    try:
        res = client.get("/api/v2/patient/devices", headers={"Authorization": "Bearer pat-tok"})
        assert res.status_code == 200, f"List devices failed: {res.text}"
        data = res.json()
        assert data["patient_id"] == mock_scoped_session
        assert isinstance(data["devices"], list)
        assert len(data["devices"]) == 1
        for dev in data["devices"]:
            assert "device_id" in dev
            assert "device_label" in dev
            assert "platform" in dev
            assert "status" in dev
            assert "enrolled_at" in dev
            assert "device_public_key" not in dev
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_request_consent_no_enrolled_device_409(mock_provider_auth):
    """Test 3: request consent returns 409 when target patient has zero active enrolled devices."""
    payload = {
        "patient_id": "123e4567-e89b-12d3-a456-426614174099",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration_seconds": 120,
    }
    with patch("app.api.v2.consent_routes.select"):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result
        from app.core.database import get_db_session
        app.dependency_overrides[get_db_session] = lambda: mock_db

        try:
            res = client.post(
                "/api/v2/consent/request",
                headers={"Authorization": "Bearer doc-tok"},
                json=payload,
            )
            assert res.status_code == 409
            assert "Patient device not enrolled" in res.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_db_session, None)


def test_request_consent_creates_pending(mock_provider_auth):
    """Test 4: request consent creates pending challenge in Redis when active device exists."""
    payload = {
        "patient_id": "123e4567-e89b-12d3-a456-426614174001",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "access_duration_seconds": 120,
    }
    mock_dev = MagicMock(spec=PatientDeviceKey)
    mock_dev.status = "active"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_dev
    mock_db.execute.return_value = mock_result

    from app.core.database import get_db_session
    app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func, \
             patch("app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock):
            mock_redis = MagicMock()
            mock_redis_func.return_value = mock_redis
            res = client.post(
                "/api/v2/consent/request",
                headers={"Authorization": "Bearer doc-tok"},
                json=payload,
            )
            assert res.status_code == 201, f"Create consent request failed: {res.text}"
            data = res.json()
            assert data["status"] == "pending"
            assert data["request_id"]
            assert data["expires_in_seconds"] == 120
            mock_redis.set.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_consent_status_returns_pending(mock_provider_auth):
    """Test 5: consent status polling returns pending when active challenge exists in Redis."""
    req_id = str(uuid.uuid4())
    stored_json = f'{{"request_id": "{req_id}", "status": "pending", "provider_id": "{mock_provider_auth.actor_uid}"}}'
    with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func:
        mock_redis = MagicMock()
        mock_redis.get.return_value = stored_json
        mock_redis_func.return_value = mock_redis

        res = client.get(f"/api/v2/consent/status/{req_id}", headers={"Authorization": "Bearer doc-tok"})
        assert res.status_code == 200
        data = res.json()
        assert data["request_id"] == req_id
        assert data["status"] == "pending"


def test_consent_status_returns_expired_after_ttl(mock_provider_auth):
    """Test 6: consent status polling returns expired when Redis challenge TTL expires / key is gone."""
    req_id = str(uuid.uuid4())
    with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func:
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # Key expired / deleted
        mock_redis_func.return_value = mock_redis

        res = client.get(f"/api/v2/consent/status/{req_id}", headers={"Authorization": "Bearer doc-tok"})
        assert res.status_code == 200
        data = res.json()
        assert data["request_id"] == req_id
        assert data["status"] == "expired"
