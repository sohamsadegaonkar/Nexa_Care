from unittest.mock import AsyncMock, patch

import pytest

from app.services.push_notification_service import PushNotificationService


@pytest.mark.asyncio
async def test_approval_notification_failure_returns_unavailable_without_type_error():
    service = PushNotificationService()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=RuntimeError("expo down")):
        result = await service.send_approval_request(
            patient_id="patient-1",
            request_id="request-1",
            provider_name="Provider",
            purpose="treatment",
            expo_push_token="ExpoPushToken[current]",
        )

    assert result.success is False
    assert result.error == "PUSH_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_emergency_notification_failure_returns_unavailable_without_type_error():
    service = PushNotificationService()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=RuntimeError("expo down")):
        result = await service.send_emergency_access_notice(
            patient_id="patient-1",
            event_id="event-1",
            expo_push_token="ExpoPushToken[current]",
        )

    assert result.success is False
    assert result.error == "PUSH_PROVIDER_UNAVAILABLE"
