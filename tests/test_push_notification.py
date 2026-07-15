import pytest
import jwt
from pydantic import ValidationError
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace
from uuid import UUID
from fastapi.testclient import TestClient
from app.services.push_notification_service import PushNotificationService
from app.api.v2.assurance_routes import PushTokenRegistration, register_push_token
from app.core.database import get_db_session
from app.core.dependencies import get_scoped_session
from app.main import app
from app.services.patient_auth_service import issue_patient_access_token


client = TestClient(app)
JWT_SECRET = "push-test-patient-secret-at-least-32-characters"


@pytest.mark.parametrize("token", ["ExponentPushToken[legacy]", "ExpoPushToken[current]"])
def test_push_token_registration_accepts_expo_token_formats(token):
    assert PushTokenRegistration(expo_push_token=token, platform="android").expo_push_token == token


def test_push_token_registration_rejects_malformed_token():
    with pytest.raises(ValidationError):
        PushTokenRegistration(expo_push_token="not-a-push-token", platform="android")


def test_push_registration_requires_a_session():
    with patch("app.core.dependencies.append_audit_log", new=AsyncMock()):
        response = client.post(
            "/api/v2/push/register-token",
            json={"expo_push_token": "ExpoPushToken[current]", "platform": "android"},
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing authorization token"


def test_push_registration_rejects_an_expired_session(monkeypatch):
    monkeypatch.setenv("PATIENT_JWT_SECRET", JWT_SECRET)
    expired = jwt.encode(
        {
            "sub": "patient-1",
            "patient_id": "patient-1",
            "actor_type": "patient",
            "auth_method": "phone_otp",
            "exp": 1,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    with patch("app.core.dependencies.append_audit_log", new=AsyncMock()), patch(
        "app.core.dependencies.validate_session_context", new=AsyncMock(return_value=None)
    ):
        response = client.post(
            "/api/v2/push/register-token",
            headers={"Authorization": f"Bearer {expired}"},
            json={"expo_push_token": "ExpoPushToken[current]", "platform": "android"},
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired session"


def test_valid_patient_session_registers_push_token(monkeypatch):
    monkeypatch.setenv("PATIENT_JWT_SECRET", JWT_SECRET)
    patient_id = "123e4567-e89b-12d3-a456-426614174001"
    token, _ = issue_patient_access_token(
        patient_id,
        "123e4567-e89b-12d3-a456-426614174099",
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = result
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        response = client.post(
            "/api/v2/push/register-token",
            headers={"Authorization": f"Bearer {token}"},
            json={"expo_push_token": "ExpoPushToken[current]", "platform": "android"},
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_scoped_session, None)

    assert response.status_code == 204
    db.add.assert_called_once()
    created = db.add.call_args.args[0]
    assert str(created.patient_id) == patient_id
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_token_is_idempotent_for_same_patient():
    patient_id = UUID("123e4567-e89b-12d3-a456-426614174001")
    existing = SimpleNamespace(
        patient_id=patient_id,
        expo_push_token="ExpoPushToken[current]",
        platform="android",
        is_active=True,
        updated_at=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [existing]
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = result

    await register_push_token(
        PushTokenRegistration(expo_push_token="ExpoPushToken[current]", platform="android"),
        patient_id=str(patient_id),
        db=db,
    )

    db.add.assert_not_called()
    db.commit.assert_awaited_once()
    assert existing.is_active is True
    assert existing.updated_at is not None


@pytest.mark.asyncio
async def test_register_token_safely_reassigns_device_between_patients():
    old = SimpleNamespace(
        patient_id=UUID("123e4567-e89b-12d3-a456-426614174001"),
        expo_push_token="ExpoPushToken[current]",
        platform="android",
        is_active=True,
        updated_at=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [old]
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = result
    new_patient = "123e4567-e89b-12d3-a456-426614174002"

    await register_push_token(
        PushTokenRegistration(expo_push_token="ExpoPushToken[current]", platform="android"),
        patient_id=new_patient,
        db=db,
    )

    assert old.is_active is False
    created = db.add.call_args.args[0]
    assert str(created.patient_id) == new_patient
    assert created.is_active is True
    db.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_send_approval_request_success():
    service = PushNotificationService()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {"status": "ok", "id": "123-456"}
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await service.send_approval_request(
            patient_id="p-1",
            request_id="req-1",
            provider_name="Strange",
            purpose="review",
            expo_push_token="ExponentPushToken[xxx]"
        )
        
        assert result.success is True
        assert result.message_id == "123-456"


@pytest.mark.asyncio
async def test_consent_push_payload_uses_canonical_route_and_generic_lock_screen_text():
    service = PushNotificationService()
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": [{"status": "ok", "id": "ticket-1"}]}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response) as post:
        await service.send_approval_request(
            patient_id="patient-secret",
            request_id="request-123",
            provider_name="Sensitive Provider Name",
            purpose="sensitive-purpose",
            expo_push_token="ExpoPushToken[current]",
        )

    payload = post.await_args.kwargs["json"]
    assert payload["data"]["request_id"] == "request-123"
    assert payload["data"]["deep_link"] == (
        "nexacare://patient/consent-request?requestId=request-123"
    )
    assert "Sensitive Provider Name" not in payload["body"]
    assert "sensitive-purpose" not in payload["body"]

@pytest.mark.asyncio
async def test_send_approval_request_expo_error():
    service = PushNotificationService()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"status": "error", "message": "Device not registered"}
        ]
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await service.send_approval_request(
            patient_id="p-1",
            request_id="req-1",
            provider_name="Strange",
            purpose="review",
            expo_push_token="ExponentPushToken[xxx]"
        )
        
        assert result.success is False
        assert result.error == "Device not registered"

@pytest.mark.asyncio
async def test_send_approval_request_http_failure():
    service = PushNotificationService()
    
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await service.send_approval_request(
            patient_id="p-1",
            request_id="req-1",
            provider_name="Strange",
            purpose="review",
            expo_push_token="ExponentPushToken[xxx]"
        )
        
        assert result.success is False
        assert result.error == "HTTP 500"
