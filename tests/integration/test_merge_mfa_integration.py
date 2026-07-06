"""Integration tests for Squad D: Merge MFA step-up.

Contract:
- Create fresh challenge.
- Verify challenge with TOTP.
- Use challenge in header for merge call.
"""

import pytest

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad D Day 4: Merge challenge flow not implemented")
@pytest.mark.asyncio
async def test_merge_requires_fresh_challenge(test_client):
    """Test: Merge without fresh challenge header -> 403."""
    resp = await test_client.post("/api/v2/patient/merge", json={
        "old_patient_uuid": "old-123",
        "canonical_patient_uuid": "new-456",
        "reason": "duplicate"
    })
    assert resp.status_code == 403

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad D Day 4")
@pytest.mark.asyncio
async def test_merge_with_verified_challenge(test_client):
    """Test: Merge with verified challenge -> 200."""
    # 1. Create challenge
    chal_resp = await test_client.post("/api/v2/auth/challenge/merge")
    challenge_token = chal_resp.json()["challenge_token"]
    
    # 2. Verify challenge
    await test_client.post("/api/v2/auth/challenge/merge/verify", json={
        "challenge_token": challenge_token,
        "totp_code": "123456"
    })
    
    # 3. Merge
    merge_resp = await test_client.post(
        "/api/v2/patient/merge",
        json={"old_patient_uuid": "old-1", "canonical_patient_uuid": "new-1", "reason": "r"},
        headers={"X-Merge-Challenge": challenge_token}
    )
    assert merge_resp.status_code == 201
