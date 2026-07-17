"""Push transport remains request/status only; decisions use signed consent."""

import pytest

from app.main import app


@pytest.mark.integration
def test_push_transport_exposes_no_decision_route():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/v2/push/{request_id}/respond" not in paths
    assert "/api/v2/consent/approve-signed" in paths


@pytest.mark.integration
def test_unknown_push_status_is_fail_closed(test_client):
    response = test_client.get("/api/v2/push/00000000-0000-4000-8000-000000000001/status")
    assert response.status_code in {401, 403}
