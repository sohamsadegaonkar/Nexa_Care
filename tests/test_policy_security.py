import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_policy_update_requires_auth():
    """Policy update should fail without authentication"""
    response = client.put("/api/v2/patient/123e4567-e89b-12d3-a456-426614174000/policy", json={
        "consent_assurance_policy": "standard"
    })
    assert response.status_code == 401

def test_policy_update_requires_role():
    """Only clinician/admin should be able to update policy"""
    # This test assumes a receptionist token
    headers = {"Authorization": "Bearer receptionist-token"}
    response = client.put(
        "/api/v2/patient/123e4567-e89b-12d3-a456-426614174000/policy",
        json={"consent_assurance_policy": "standard"},
        headers=headers
    )
    assert response.status_code == 403

def test_policy_update_blocked_in_production():
    """Policy updates should be blocked unless ALLOW_DEV_POLICY_UPDATES=true"""
    headers = {"Authorization": "Bearer clinician-token"}
    response = client.put(
        "/api/v2/patient/123e4567-e89b-12d3-a456-426614174000/policy",
        json={"consent_assurance_policy": "standard"},
        headers=headers
    )
    # Should be 403 if ALLOW_DEV_POLICY_UPDATES is not set
    assert response.status_code in [403, 401]