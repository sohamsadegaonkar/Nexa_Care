import os
import pytest
from fastapi.testclient import TestClient
from uuid import uuid4

# Import your FastAPI app
from app.main import app 

# Ensure the provider key is set for testing
os.environ["PROVIDER_API_KEY"] = "test-provider-key-123"
client = TestClient(app)

HEADERS = {
    "X-Provider-Key": "test-provider-key-123"
}

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_security_perimeter_rejects_unauthorized():
    # Attempting to register without the X-Provider-Key should fail
    payload = {
        "patient_name": "Test User",
        "phone": "555-0100",
        "aadhaar_abha_id": "1234-5678-9012",
        "diagnoses": ["Hypertension"],
        "lab_results": [],
        "prescriptions": []
    }
    response = client.post("/register", json=payload)
    assert response.status_code == 403

def test_full_patient_lifecycle():
    # 1. Register a Patient
    payload = {
        "patient_name": "Test User Lifecycle",
        "phone": "555-0101",
        "aadhaar_abha_id": "SYNTHETIC-ID-999",
        "diagnoses": ["Type 2 Diabetes"],
        "lab_results": ["HbA1c: 7.2%"],
        "prescriptions": ["Metformin 500mg"]
    }
    reg_response = client.post("/register", json=payload, headers=HEADERS)
    assert reg_response.status_code == 200, f"Registration failed: {reg_response.text}"
    
    data = reg_response.json()
    assert "pii_vault" in data
    masked_id = data["pii_vault"]["masked_internal_id"]
    assert masked_id is not None

    # 2. Request Consent Token
    consent_payload = {
        "masked_internal_id": masked_id,
        "duration_seconds": 300
    }
    consent_response = client.post("/request-consent", json=consent_payload, headers=HEADERS)
    assert consent_response.status_code == 200
    token = consent_response.json().get("consent_token")
    assert token is not None

    # 3. View Record (Reassembly)
    view_response = client.get(f"/view-record?consent_token={token}", headers=HEADERS)
    assert view_response.status_code == 200
    
    view_data = view_response.json()
    assert view_data["masked_internal_id"] == masked_id
    # Verify Redaction worked!
    assert view_data["pii"]["phone"] != "555-0101" # Should be masked
    assert "Type 2 Diabetes" in view_data["clinical"]["diagnoses"]