"""Integration guards for the retired weak push-response protocol."""

import pytest


@pytest.mark.integration
@pytest.mark.parametrize("decision", ["approved", "denied"])
def test_legacy_push_response_endpoint_is_not_registered(test_client, decision: str):
    response = test_client.post(
        "/api/v2/push/00000000-0000-4000-8000-000000000001/respond",
        json={"decision": decision, "signature": "unbound", "nonce": "unbound"},
    )
    assert response.status_code == 404


@pytest.mark.integration
def test_legacy_push_response_replay_surface_is_absent(test_client):
    path = "/api/v2/push/00000000-0000-4000-8000-000000000001/respond"
    assert test_client.post(path, json={}).status_code == 404
    assert test_client.post(path, json={}).status_code == 404
