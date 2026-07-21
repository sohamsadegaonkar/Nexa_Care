"""Service for sending push notifications via Expo Push API."""

from __future__ import annotations

import hashlib
import logging
import httpx
from dataclasses import dataclass
from app.observability.safe_exceptions import log_safe_exception

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
            "body": "A verified provider requested access. Open Nexa Care to review.",
            "data": {
                "type": "consent_approval",
                "request_id": request_id,
                "deep_link": f"nexacare://patient/consent-request?requestId={request_id}",
            },
            "sound": "default",
            "priority": "high",
            "channelId": "consent-requests",
        }

        patient_ref = _safe_ref(patient_id)
        request_ref = _safe_ref(request_id)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(EXPO_PUSH_API_URL, json=payload)

                if response.status_code == 200:
                    result_data = response.json()
                    # A single message returns one ticket object; batch sends
                    # return a list. Expo can return HTTP 200 for ticket errors.
                    raw_data = result_data.get("data")
                    ticket = (
                        raw_data[0]
                        if isinstance(raw_data, list) and raw_data
                        else raw_data
                        if isinstance(raw_data, dict)
                        else {}
                    )
                    if ticket.get("status") == "ok":
                        logger.info(
                            "push_notification_sent",
                            extra={
                                "patient_ref": patient_ref,
                                "request_ref": request_ref,
                            },
                        )
                        return PushDeliveryResult(
                            success=True, message_id=ticket.get("id")
                        )
                    else:
                        logger.error(
                            "push_notification_delivery_failed",
                            extra={
                                "patient_ref": patient_ref,
                                "request_ref": request_ref,
                            },
                        )
                        return PushDeliveryResult(
                            success=False, error="PUSH_PROVIDER_REJECTED"
                        )
                else:
                    logger.error(
                        "expo_push_api_error",
                        extra={
                            "request_ref": request_ref,
                            "status_code": response.status_code,
                        },
                    )
                    return PushDeliveryResult(
                        success=False, error=f"HTTP {response.status_code}"
                    )

        except Exception as exc:
            log_safe_exception(
                logger,
                exc,
                subsystem="push_notification",
                operation="send_approval_request",
                fields={"correlation_id": request_ref},
            )
            return PushDeliveryResult(success=False, error="PUSH_PROVIDER_UNAVAILABLE")

    async def send_emergency_access_notice(
        self,
        *,
        patient_id: str,
        event_id: str,
        expo_push_token: str,
    ) -> PushDeliveryResult:
        payload = {
            "to": expo_push_token,
            "title": "Emergency record access",
            "body": "Emergency access to limited health information was granted. Open Nexa Care for details.",
            "data": {"type": "break_glass_notice", "event_id": event_id},
            "sound": "default",
            "priority": "high",
        }
        event_ref = _safe_ref(event_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(EXPO_PUSH_API_URL, json=payload)
            if response.status_code != 200:
                return PushDeliveryResult(success=False, error="PUSH_PROVIDER_REJECTED")
            data = response.json().get("data", {})
            ticket = data[0] if isinstance(data, list) and data else data
            if isinstance(ticket, dict) and ticket.get("status") == "ok":
                return PushDeliveryResult(success=True, message_id=ticket.get("id"))
            return PushDeliveryResult(success=False, error="PUSH_PROVIDER_REJECTED")
        except Exception as exc:
            log_safe_exception(
                logger,
                exc,
                subsystem="push_notification",
                operation="send_emergency_access_notice",
                fields={"correlation_id": event_ref},
            )
            return PushDeliveryResult(success=False, error="PUSH_PROVIDER_UNAVAILABLE")
