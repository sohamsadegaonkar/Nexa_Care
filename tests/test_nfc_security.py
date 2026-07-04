import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_nfc_resolve_requires_auth():
    response = client.post("/api/v2/nfc/resolve", json={
        "card_uid": "TESTCARD123"
    })
    assert response.status_code == 401

def test_nfc_card_uid_sanitized():
    """Card UID should be sanitized (uppercased and stripped)"""
    # This would require a valid auth token in real test
    # For now we just verify the model accepts the input
    from app.api.v2.nfc_routes import NFCResolveRequest
    req = NFCResolveRequest(card_uid="  testcard123  ")
    assert req.card_uid == "TESTCARD123"