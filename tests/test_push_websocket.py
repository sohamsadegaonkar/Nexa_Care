import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_redis():
    m = MagicMock()  # Use MagicMock for the client
    # Mock pubsub
    mock_pubsub = AsyncMock()
    m.pubsub.return_value = mock_pubsub
    m.config_get = AsyncMock(return_value={"notify-keyspace-events": "K$gx"})
    return m


@pytest.mark.asyncio
async def test_ws_rejects_without_feature_flag(client):
    with patch("app.api.v2.assurance_routes.PUSH_STATUS_TRANSPORT", "poll"):
        with pytest.raises(
            Exception
        ):  # TestClient raises for closed WS during handshake if not handled
            with client.websocket_connect("/api/v2/push/req-1/ws?token=valid"):
                pass


@pytest.mark.asyncio
async def test_ws_authentication_failure(client):
    with patch("app.api.v2.assurance_routes.PUSH_STATUS_TRANSPORT", "websocket"):
        with patch(
            "app.api.v2.assurance_routes.resolve_provider_session_context",
            return_value=None,
        ):
            with pytest.raises(Exception):
                with client.websocket_connect("/api/v2/push/req-1/ws?token=invalid"):
                    pass


@pytest.mark.asyncio
async def test_ws_clear_error_when_keyspace_notifications_disabled(client, mock_redis):
    mock_redis.config_get = AsyncMock(return_value={"notify-keyspace-events": ""})
    with (
        patch("app.api.v2.assurance_routes.PUSH_STATUS_TRANSPORT", "websocket"),
        patch(
            "app.api.v2.assurance_routes.resolve_provider_session_context",
            return_value={"authenticated": True},
        ),
        patch(
            "app.services.consent_engine.get_consent_redis_client",
            return_value=mock_redis,
        ),
    ):
        with client.websocket_connect(
            "/api/v2/push/req-disabled/ws?token=t"
        ) as websocket:
            data = websocket.receive_json()
            assert data["status"] == "websocket_unavailable"
            assert data["fallback"] == "poll"
            assert "keyspace notifications" in data["detail"]


@pytest.mark.asyncio
async def test_ws_happy_path_resolution(client, mock_redis):
    request_id = "req-happy"
    token = "valid-token"

    # Mock successful auth
    mock_provider = {"provider_id": "doc-1", "authenticated": True}

    # Mock status sequence: pending -> approved
    mock_status_pending = {"request_id": request_id, "status": "pending"}
    mock_status_approved = {"request_id": request_id, "status": "approved"}

    with (
        patch("app.api.v2.assurance_routes.PUSH_STATUS_TRANSPORT", "websocket"),
        patch(
            "app.api.v2.assurance_routes.resolve_provider_session_context",
            return_value=mock_provider,
        ),
        patch(
            "app.services.consent_engine.get_consent_redis_client",
            return_value=mock_redis,
        ),
        patch(
            "app.services.assurance_service.AssuranceService.get_push_status"
        ) as mock_get_status,
    ):
        mock_get_status.side_effect = [mock_status_pending, mock_status_approved]

        # Simulate a Redis message arriving
        mock_redis.pubsub.return_value.get_message.side_effect = [
            {"type": "message", "data": "set"},  # resolution event
            None,
        ]

        with client.websocket_connect(
            f"/api/v2/push/{request_id}/ws?token={token}"
        ) as websocket:
            # 1. Initial status
            data = websocket.receive_json()
            assert data["status"] == "pending"

            # 2. Updated status after "message"
            data = websocket.receive_json()
            assert data["status"] == "approved"


@pytest.mark.asyncio
async def test_ws_initial_resolved_closes_immediately(client, mock_redis):
    request_id = "req-already-done"
    mock_provider = {"authenticated": True}
    mock_status = {"request_id": request_id, "status": "denied"}

    with (
        patch("app.api.v2.assurance_routes.PUSH_STATUS_TRANSPORT", "websocket"),
        patch(
            "app.api.v2.assurance_routes.resolve_provider_session_context",
            return_value=mock_provider,
        ),
        patch(
            "app.services.consent_engine.get_consent_redis_client",
            return_value=mock_redis,
        ),
        patch(
            "app.services.assurance_service.AssuranceService.get_push_status",
            return_value=mock_status,
        ),
    ):
        with client.websocket_connect("/api/v2/push/req/ws?token=t") as websocket:
            data = websocket.receive_json()
            assert data["status"] == "denied"
            # Should be closed by server now
            with pytest.raises(Exception):
                websocket.receive_json()
