from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_assurance_push_requires_auth():
    response = client.post("/api/v2/assurance/push/request", json={
        "patient_uuid": "123e4567-e89b-12d3-a456-426614174000",
        "clinician_name": "Dr. Test",
        "hospital_name": "Test Hospital",
        "purpose": "ROUTINE"
    })
    assert response.status_code == 401

def test_assurance_biometric_requires_auth():
    response = client.post("/api/v2/assurance/biometric/verify", json={
        "patient_uuid": "123e4567-e89b-12d3-a456-426614174000",
        "biometric_token": "test-token"
    })
    assert response.status_code == 401

def test_break_glass_requires_auth():
    response = client.post("/api/v2/consent/break-glass/issue", json={
        "patient_uuid": "123e4567-e89b-12d3-a456-426614174000",
        "hospital_id": "H001",
        "clinician_id": "C001",
        "reason": "EMERGENCY",
        "justification": "Test"
    })
    assert response.status_code == 401