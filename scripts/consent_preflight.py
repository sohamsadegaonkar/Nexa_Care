#!/usr/bin/env python3
"""Read-only alpha consent readiness checks.

The ephemeral P-256 self-test verifies cryptographic runtime support only. It
is never presented as evidence of physical-device enrollment.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import get_session_factory  # noqa: E402
from app.core.redis import ping_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.models.patient_device_keys import PatientDeviceKey  # noqa: E402
from scripts.demo_environment import require_demo_environment  # noqa: E402

DEMO_PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"


def redact(value: str | None) -> str:
    if not value:
        return "[NONE]"
    return f"{value[:4]}***{value[-4:]}" if len(value) > 8 else f"{value[:2]}***"


async def run_preflight() -> bool:
    require_demo_environment("consent_preflight")
    print("NEXA CARE ALPHA CONSENT PREFLIGHT")
    all_go = True

    try:
        all_go = bool(ping_redis()) and all_go
        print("redis=reachable" if all_go else "redis=unavailable")
    except Exception:
        print("redis=unavailable")
        all_go = False

    try:
        response = TestClient(app, base_url="http://localhost").get("/health")
        print(f"api_health_status={response.status_code}")
        all_go = response.status_code == 200 and all_go
    except Exception:
        print("api_health_status=unavailable")
        all_go = False

    if not os.getenv("DATABASE_URL"):
        print("physical_device_check=unavailable database_not_configured")
        all_go = False
    else:
        try:
            async with get_session_factory()() as db:
                result = await db.execute(select(PatientDeviceKey).where(
                    PatientDeviceKey.patient_id == uuid.UUID(DEMO_PATIENT_ID),
                    PatientDeviceKey.status == "active",
                ).limit(1))
                device = result.scalar_one_or_none()
            print(
                f"physical_device_check=active device_id={redact(str(device.id))}"
                if device else "physical_device_check=missing"
            )
            all_go = device is not None and all_go
        except Exception:
            print("physical_device_check=unavailable")
            all_go = False

    try:
        private_key = ec.generate_private_key(ec.SECP256R1())
        payload = b"nexa-care-ephemeral-runtime-self-test"
        signature = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
        private_key.public_key().verify(signature, payload, ec.ECDSA(hashes.SHA256()))
        print("p256_runtime_self_test=passed not_enrollment_evidence=true")
    except Exception:
        print("p256_runtime_self_test=failed")
        all_go = False

    print(f"preflight={'GO' if all_go else 'NO-GO'}")
    return all_go


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(run_preflight()) else 1)
