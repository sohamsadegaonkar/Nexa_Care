import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.push_notification_service import PushNotificationService

@pytest.mark.asyncio
async def test_send_approval_request_success():
    service = PushNotificationService()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"status": "ok", "id": "123-456"}
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
        
        assert result.success is True
        assert result.message_id == "123-456"

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
