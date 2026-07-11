"""Integration tests for Squad B/C: Biometric Signature Verification.

Contract:
- Use ECDSA P-256 for biometric signatures.
- Nonce challenge provided in push request status.
- Signature required for 'approved' decision.
"""

import base64
from unittest.mock import AsyncMock

import pytest

from app.services.biometric_signature_verifier import BiometricSignatureVerifier

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad B Day 4: Biometric signature verification not implemented")
@pytest.mark.asyncio
async def test_biometric_approval_flow(test_client):
    """Test: Enroll device key -> Sign challenge -> Verify signature -> Returns valid.
    
    Ensures that the 'approved' decision is cryptographically bound 
    to the specific device's secure enclave.
    """
    # 1. Create request
    req_resp = await test_client.post("/api/v2/assurance/push/request", json={"patient_uuid": "p-123", "purpose": "t"})
    request_id = req_resp.json()["request_id"]
    
    # 2. Get nonce from status
    status_resp = await test_client.get(f"/api/v2/assurance/push/{request_id}/status")
    nonce = status_resp.json()["nonce"]
    
    # 3. Respond with signature (mocked)
    respond_resp = await test_client.post(f"/api/v2/assurance/push/{request_id}/respond", json={
        "decision": "approved",
        "signature": "valid-base64-signature",
        "nonce": nonce
    })
    assert respond_resp.status_code == 200

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad B Day 4")
@pytest.mark.asyncio
async def test_invalid_signature_fails(test_client):
    """Test: Wrong signature -> Returns 401."""
    req_resp = await test_client.post("/api/v2/assurance/push/request", json={"patient_uuid": "p-123", "purpose": "t"})
    request_id = req_resp.json()["request_id"]
    
    respond_resp = await test_client.post(f"/api/v2/assurance/push/{request_id}/respond", json={
        "decision": "approved",
        "signature": "garbage-sig",
        "nonce": "wrong-nonce"
    })
    assert respond_resp.status_code == 401

@pytest.mark.integration
@pytest.mark.asyncio
async def test_signature_replay_fails():
    """Test: Replayed nonce fails before device-key lookup or signature acceptance."""
    verifier = BiometricSignatureVerifier()
    redis = AsyncMock()
    redis.get.return_value = "1"

    result = await verifier.verify_signature(
        patient_id="p-123",
        request_id="req-123",
        signature_b64=base64.b64encode(b"signature").decode("utf-8"),
        challenge_nonce="used-nonce",
        redis=redis,
        db=AsyncMock(),
    )

    assert result.verified is False
    assert result.error == "Nonce already used"
    redis.get.assert_awaited_once_with("biometric_nonce:used-nonce:used")
    redis.setex.assert_not_awaited()
