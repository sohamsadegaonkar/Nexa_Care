"""Service for sending push notifications via Expo Push API."""

from __future__ import annotations

import hashlib
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger("nexa_logger")

EXPO_PUSH_API_URL = "https://exp.host/--/api/v2/push/send"


def _safe_ref(value: str) -> str:
    """Return a short non-reversible reference for log correlation."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


@dataclass
class PushDeliveryResult:
    success: bool
    message_id: str | None = None
    error: str | None = None

class PushNotificationService:
    """Delivers notifications to Expo/FCM/APNS."""

    async def send_approval_request(
        self,
        patient_id: str,
        request_id: str,
        provider_name: str,
        purpose: str,
        expo_push_token: str,
    ) -> PushDeliveryResult:
        """
        Send a consent approval request notification to a patient's device.
        """
        payload = {
            "to": expo_push_token,
            "title": "Consent Request",
            "body": f"Dr. {provider_name} is requesting access to your records for {purpose}",
            "data": {
                "type": "consent_approval",
                "request_id": request_id,
                "deep_link": f"nexacare://push-approval/{request_id}"
            },
            "sound": "default",
            "priority": "high",
            "channelId": "consent-requests"
        }

        patient_ref = _safe_ref(patient_id)
        request_ref = _safe_ref(request_id)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(EXPO_PUSH_API_URL, json=payload)
                
                if response.status_code == 200:
                    result_data = response.json()
                    # Expo returns 200 even if individual messages fail
                    # Data is a list of results
                    data = result_data.get("data", [])
                    if data and data[0].get("status") == "ok":
                        logger.info("push_notification_sent", extra={"patient_ref": patient_ref, "request_ref": request_ref})
                        return PushDeliveryResult(success=True, message_id=data[0].get("id"))
                    else:
                        error_msg = data[0].get("message") if data else "Unknown Expo error"
                        logger.error("push_notification_delivery_failed", extra={"patient_ref": patient_ref, "request_ref": request_ref, "error": error_msg})
                        return PushDeliveryResult(success=False, error=error_msg)
                else:
                    logger.error("expo_push_api_error", extra={"request_ref": request_ref, "status_code": response.status_code})
                    return PushDeliveryResult(success=False, error=f"HTTP {response.status_code}")

        except Exception as exc:
            logger.error("push_notification_exception", extra={"patient_ref": patient_ref, "request_ref": request_ref, "error": str(exc)})
            return PushDeliveryResult(success=False, error=str(exc))
