"""Cross-surface security seams for the production consent chain.

Cryptographic approval behavior is exercised in test_signed_approval.py;
these tests prevent the former weak response seam from returning.
"""

import pytest

from app.main import app


@pytest.mark.integration
def test_full_chain_uses_canonical_signed_approval_route():
    routes = {(method, route.path) for route in app.routes for method in getattr(route, "methods", set())}
    assert ("POST", "/api/v2/consent/approve-signed") in routes
    assert ("POST", "/api/v2/push/{request_id}/respond") not in routes


@pytest.mark.integration
def test_full_chain_rejects_unbound_three_field_decision(test_client):
    response = test_client.post(
        "/api/v2/push/00000000-0000-4000-8000-000000000001/respond",
        json={"decision": "approved", "signature": "sig", "nonce": "nonce"},
    )
    assert response.status_code == 404
