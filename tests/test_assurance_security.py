"""Security regression tests for the push-approval consent flow.

Rewritten 2026-07-06: the old /api/v2/assurance/push/request and
/api/v2/assurance/biometric/verify endpoints this file used to test were
retired when the notification rework replaced them with the Expo-push +
signed-response design in app/api/v2/assurance_routes.py (router prefix is
now /api/v2/push). There is no standalone biometric/verify endpoint any
more -- signature verification is folded into POST /{request_id}/respond
as a single atomic step. See tests/test_route_registration.py for the
corresponding EXPECTED_ROUTES reconciliation.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_push_request_requires_auth():
    """Doctor-initiated push requests require an authenticated provider."""
    response = client.post("/api/v2/push/request", json={
        "patient_id": "123e4567-e89b-12d3-a456-426614174000",
        "provider_id": "prov-001",
        "purpose": "ROUTINE",
        "scope": "clinical.diagnoses",
    })
    assert response.status_code == 401


def test_push_respond_requires_auth():
    """Responding to a push request requires an authenticated patient session.

    Biometric signature verification happens inside this same call (see
    BiometricSignatureVerifier.verify_signature in assurance_routes.py), so
    an unauthenticated caller never even reaches that check -- the session
    guard (get_scoped_session) rejects first.
    """
    response = client.post(
        "/api/v2/push/some-request-id/respond",
        json={
            "decision": "approved",
            "signature": "deadbeef",
            "nonce": "test-nonce",
        },
    )
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