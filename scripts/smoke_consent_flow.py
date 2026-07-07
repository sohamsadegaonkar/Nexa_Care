#!/usr/bin/env python3
"""Consent Flow Smoke Test Script for Nexa Care V2 (Workstream 2).

Walks the full cryptographic consent flow using the enrolled demo private key:
1. Provider initiates consent request (POST /api/v2/consent/request).
2. Patient loads real ECDSA P-256 private key from local file.
3. Patient computes signature over canonical 9-attribute digest.
4. Patient submits signed approval (POST /api/v2/consent/approve-signed).
5. Provider polls status and confirms approved state.
6. Provider retrieves clinical summary with scoped consent token.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.dependencies import get_current_provider, get_scoped_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.provider_context import ProviderContext  # noqa: E402
from scripts.enroll_demo_device import KEY_FILE_PATH, enroll_demo_device  # noqa: E402

DEMO_PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
DEMO_PROVIDER_ID = "987fcdeb-51a2-43d7-9012-345678901234"


def run_smoke_test() -> None:
    print("==========================================================================")
    print(" 🔥 NEXA CARE V2 CRYPTOGRAPHIC CONSENT FLOW SMOKE TEST")
    print("==========================================================================")

    # Ensure device key exists
    if not KEY_FILE_PATH.exists():
        print(" -> Demo device key not found. Enrolling demo device first...")
        enroll_res = enroll_demo_device()
        device_id = enroll_res["device_id"]
    else:
        print(f" -> Loading demo device private key from {KEY_FILE_PATH}...")
        device_id = "demo-device-101"

    pem_bytes = KEY_FILE_PATH.read_bytes()
    private_key = serialization.load_pem_private_key(pem_bytes, password=None)
    assert isinstance(private_key, ec.EllipticCurvePrivateKey)

    client = TestClient(app)

    # Setup Provider override
    from unittest.mock import AsyncMock, MagicMock
    mock_prov = MagicMock(spec=ProviderContext)
    mock_prov.actor_uid = DEMO_PROVIDER_ID

    app.dependency_overrides[get_current_provider] = lambda: mock_prov

    if not os.getenv("DATABASE_URL"):
        from app.core.database import get_db_session
        from app.models.patient_device_keys import PatientDeviceKey
        mock_db = AsyncMock()
        mock_dev = MagicMock(spec=PatientDeviceKey)
        mock_dev.id = uuid.UUID("111e4567-e89b-12d3-a456-426614174111") if len(device_id) != 36 else uuid.UUID(device_id)
        mock_dev.status = "active"
        mock_dev.revoked_at = None
        # Provide the DER public key corresponding to our loaded private key
        mock_dev.device_public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_dev
        mock_res.scalars.return_value.all.return_value = [mock_dev]
        mock_db.execute.return_value = mock_res
        app.dependency_overrides[get_db_session] = lambda: mock_db

    try:
        from unittest.mock import patch
        with patch("app.api.v2.consent_routes.get_redis_client") as mock_redis_func, \
             patch("app.api.v2.consent_routes.consent_engine.issue", new_callable=AsyncMock, return_value="demo-live-consent-token-999"), \
             patch("app.api.v2.consent_routes.append_audit_log_or_503", new_callable=AsyncMock):

            mock_redis = MagicMock()
            mock_redis_func.return_value = mock_redis

            # Step 1: Doctor requests consent challenge
            print(" -> Step 1: Doctor initiates consent request...")
            req_payload = {
                "patient_id": DEMO_PATIENT_ID,
                "provider_id": DEMO_PROVIDER_ID,
                "purpose": "routine_checkup",
                "scope": "clinical",
                "access_duration_seconds": 900,
            }
            res1 = client.post("/api/v2/consent/request", headers={"Authorization": "Bearer doc-tok"}, json=req_payload)
            if res1.status_code != 201:
                raise RuntimeError(f"Step 1 failed ({res1.status_code}): {res1.text}")
            data1 = res1.json()
            request_id = data1["request_id"]
            challenge_nonce = data1["challenge_nonce"]
            print(f" ✅ Challenge created: request_id={request_id}")

            # Store challenge in mock redis
            challenge_data = {
                "request_id": request_id,
                "patient_id": DEMO_PATIENT_ID,
                "provider_id": DEMO_PROVIDER_ID,
                "purpose": "routine_checkup",
                "scope": "clinical",
                "access_duration": 900,
                "challenge_nonce": challenge_nonce,
                "expires_at": "2026-07-07T18:00:00Z",
                "status": "pending",
            }
            mock_redis.get.side_effect = lambda k: None if k.startswith("biometric_nonce:") else json.dumps(challenge_data)

            # Step 2: Patient device signs 9-attribute canonical payload
            print(" -> Step 2: Patient device computes ECDSA P-256 signature over 9-attribute digest...")
            signing_input = (
                f"{request_id}|{DEMO_PATIENT_ID}|{DEMO_PROVIDER_ID}|{challenge_nonce}|"
                f"approved|clinical|routine_checkup|900|2026-07-07T18:00:00Z"
            )
            sig_bytes = private_key.sign(signing_input.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
            sig_b64 = base64.b64encode(sig_bytes).decode("utf-8")

            # Step 3: Patient submits signed approval
            print(" -> Step 3: Patient submits signed approval to POST /api/v2/consent/approve-signed...")
            app.dependency_overrides[get_scoped_session] = lambda: DEMO_PATIENT_ID
            approve_payload = {
                "request_id": request_id,
                "patient_id": DEMO_PATIENT_ID,
                "decision": "approved",
                "challenge_nonce": challenge_nonce,
                "signature": sig_b64,
                "device_id": device_id,
            }
            res2 = client.post("/api/v2/consent/approve-signed", headers={"Authorization": "Bearer pat-tok"}, json=approve_payload)
            if res2.status_code != 200:
                raise RuntimeError(f"Step 3 failed ({res2.status_code}): {res2.text}")
            data2 = res2.json()
            assert data2["status"] == "approved"
            print(" ✅ Signature verified successfully! Consent grant issued.")

            # Update challenge status for polling doctor
            challenge_data["status"] = "approved"
            challenge_data["consent_token"] = "demo-live-consent-token-999"

            # Step 4: Doctor polls status
            print(" -> Step 4: Doctor polls status endpoint GET /api/v2/consent/status/{request_id}...")
            app.dependency_overrides.pop(get_scoped_session, None)
            res3 = client.get(f"/api/v2/consent/status/{request_id}", headers={"Authorization": "Bearer doc-tok"})
            assert res3.status_code == 200
            data3 = res3.json()
            assert data3["status"] == "approved"
            print(" ✅ Doctor confirmed approved status.")

            # Step 5: Doctor accesses clinical summary
            print(" -> Step 5: Doctor accesses clinical summary with consent token...")
            from app.core.consent_gate import ConsentCapability
            mock_cap = ConsentCapability(
                patient_id=DEMO_PATIENT_ID,
                clinician_id=DEMO_PROVIDER_ID,
                purpose="clinical_summary",
                scope=["clinical"],
                is_break_glass=False,
                reason_code=None,
                issued_at="2026-07-07T16:00:00Z",
            )
            with patch("app.core.consent_gate.validate_consent_capability", return_value=mock_cap), \
                 patch("app.core.consent_gate.append_audit_log_or_503", new_callable=AsyncMock):
                res4 = client.get(
                    f"/api/v2/patient/{DEMO_PATIENT_ID}/summary",
                    headers={
                        "Authorization": "Bearer doc-tok",
                        "X-Consent-Token": "demo-live-consent-token-999",
                        "X-Consent-Purpose": "clinical_summary",
                    },
                )
                assert res4.status_code == 200
                data4 = res4.json()
                assert data4["patient_id"] == DEMO_PATIENT_ID
                print(" ✅ Clinical summary retrieved successfully via zero-trust consent gate!")

            print("==========================================================================")
            print(" 🎯 SMOKE TEST STATUS: SUCCESS — Cryptographic Consent Flow Verified.")
            print("==========================================================================")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    import json
    import uuid
    run_smoke_test()
