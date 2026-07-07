"""Integration tests for Squad D: Tombstone Redirects.

Contract:
- Resolve merged card to canonical ID.
- Flag is_redirected for UI warning.
- Block inactive cards.
"""

import pytest

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad D Day 1: NFC Merge redirects not implemented")
@pytest.mark.asyncio
async def test_merged_card_redirect(test_client):
    """Test: Scan merged patient's old card -> Returns canonical_patient_id.
    
    The backend should look up the tombstone table and find the 
    active destination record.
    """
    resp = await test_client.post("/api/v2/nfc/resolve", json={
        "card_uid": "OLD-CARD-UID"
    })
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_redirected"] is True
    assert data["canonical_patient_id"] is not None

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad D Day 1")
@pytest.mark.asyncio
async def test_inactive_card_rejected(test_client):
    """Test: Scan inactive card -> Returns 403."""
    resp = await test_client.post("/api/v2/nfc/resolve", json={
        "card_uid": "INACTIVE-CARD-UID"
    })
    assert resp.status_code == 403
