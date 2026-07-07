"""Integration tests for Squad A: Consent Revocation.

Contract:
- Issue break-glass token.
- Revoke it via dedicated endpoint.
- Verify validation now fails.
"""

import pytest

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad A Day 2: Revocation logic not implemented")
@pytest.mark.asyncio
async def test_issue_and_revoke_break_glass(test_client):
    """Test: Issue a break-glass token -> Revoke it -> Validate returns 403.
    
    The revocation endpoint should immediately invalidate the token in Redis
    and mark it as revoked in the Postgres audit log.
    """
    # 1. Issue break-glass
    issue_resp = await test_client.post("/api/v2/consent/break-glass/issue", json={
        "patient_id": "p-123",
        "reason_code": "EMERGENCY_ER",
    })
    token = issue_resp.json()["consent_token"]
    
    # 2. Revoke it
    revoke_resp = await test_client.post("/api/v2/consent/break-glass/revoke", json={
        "consent_token": token,
        "revocation_reason": "Manual override"
    })
    assert revoke_resp.status_code == 200
    
    # 3. Validate
    val_resp = await test_client.get(f"/api/v2/consent/validate?consent_token={token}")
    assert val_resp.status_code == 401 # or 403 depending on implementation

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad A Day 2")
@pytest.mark.asyncio
async def test_revoke_non_break_glass_fails(test_client):
    """Test: Revoke a non-break-glass token -> Returns 400.
    
    Routine tokens are single-use and short-lived; the revocation 
    endpoint is currently intended only for long-lived/audit-heavy 
    emergency grants.
    """
    issue_resp = await test_client.post("/api/v2/consent/routine/issue", json={
        "patient_id": "p-123",
        "purpose": "TREATMENT"
    })
    token = issue_resp.json()["consent_token"]
    
    revoke_resp = await test_client.post("/api/v2/consent/break-glass/revoke", json={
        "consent_token": token,
        "revocation_reason": "testing"
    })
    assert revoke_resp.status_code == 400

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad A Day 2")
@pytest.mark.asyncio
async def test_revoke_forged_token(test_client):
    """Test: Revoke with forged token -> Returns 403 or 404."""
    revoke_resp = await test_client.post("/api/v2/consent/break-glass/revoke", json={
        "consent_token": "forged-token-123",
        "revocation_reason": "testing"
    })
    assert revoke_resp.status_code in [403, 404, 400]
