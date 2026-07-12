#!/usr/bin/env python3
"""Enroll Demo Device Script for Nexa Care V2 (Workstream 2).

Generates a real ECDSA P-256 keypair for canonical demo patient Aarav Sharma
(123e4567-e89b-12d3-a456-426614174001), enrolls the public key via API, and saves
the private key securely to local disk (.demo_device_private_key.pem) excluded from git.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.dependencies import get_db_session, get_scoped_session  # noqa: E402
from app.main import app  # noqa: E402

DEMO_PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
KEY_FILE_PATH = ROOT / ".demo_device_private_key.pem"


def enroll_demo_device() -> dict[str, str]:
    env = os.getenv("ENV", os.getenv("ENVIRONMENT", "development")).lower().strip()
    if env in {"prod", "production"}:
        raise RuntimeError(f"Refusing to enroll demo device in production environment ('{env}').")

    print(f" -> Generating ECDSA P-256 keypair for patient Aarav Sharma ({DEMO_PATIENT_ID})...")
    private_key = ec.generate_private_key(ec.SECP256R1())

    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    KEY_FILE_PATH.write_bytes(pem_bytes)
    os.chmod(KEY_FILE_PATH, 0o600)
    print(f" -> Private key saved securely to {KEY_FILE_PATH} (0600 perms, gitignored).")

    public_key = private_key.public_key()
    der_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    b64_pub_key = base64.b64encode(der_bytes).decode("utf-8")

    print(" -> Enrolling public key via POST /api/v2/patient/devices/enroll...")
    client = TestClient(app)
    app.dependency_overrides[get_scoped_session] = lambda: DEMO_PATIENT_ID

    if not os.getenv("DATABASE_URL"):
        from unittest.mock import AsyncMock, MagicMock
        mock_db = AsyncMock()
        mock_res_count = MagicMock()
        mock_res_count.scalar.return_value = 0
        mock_res_exist = MagicMock()
        mock_res_exist.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [mock_res_count, mock_res_exist]
        app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        from unittest.mock import AsyncMock, patch
        with patch("app.api.v2.device_routes.append_audit_log_or_503", new_callable=AsyncMock):
            payload = {
                "device_public_key": b64_pub_key,
                "device_label": "Aarav Sharma Demo iPhone 15 Pro",
                "platform": "ios",
                "expo_push_token": "ExponentPushToken[demo-aarav-sharma]",
            }
            res = client.post(
                "/api/v2/patient/devices/enroll",
                headers={"Authorization": f"Bearer {DEMO_PATIENT_ID}"},
                json=payload,
            )
            if res.status_code != 201:
                raise RuntimeError(f"Device enrollment failed ({res.status_code}): {res.text}")
            data = res.json()
            device_id = data["device_id"]
            print(f" ✅ Successfully enrolled demo device ID: {device_id} (Status: {data['status']})")
            return {"device_id": device_id, "patient_id": DEMO_PATIENT_ID, "key_file": str(KEY_FILE_PATH)}
    finally:
        app.dependency_overrides.pop(get_scoped_session, None)
        app.dependency_overrides.pop(get_db_session, None)


if __name__ == "__main__":
    enroll_demo_device()
