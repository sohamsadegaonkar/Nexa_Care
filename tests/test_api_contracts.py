"""Current API surface and fail-closed contract tests."""

import uuid

import pytest

from app.main import app


def routes():
    return {(method, route.path) for route in app.routes for method in getattr(route, "methods", set())}


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v2/consent/request"),
    ("POST", "/api/v2/consent/approve-signed"),
    ("POST", "/api/v2/consent/{request_id}/claim-access"),
    ("GET", "/api/v2/consent/history/self"),
    ("GET", "/api/v2/patient/{id}/summary"),
    ("GET", "/api/v2/patient/{id}/timeline"),
    ("POST", "/api/v2/pipeline/documents/upload"),
    ("GET", "/api/v2/pipeline/jobs/{job_id}"),
    ("POST", "/api/v2/pipeline/fields/{field_id}/review"),
    ("POST", "/api/v2/pipeline/jobs/{job_id}/commit"),
])
def test_required_routes_registered(method, path):
    assert (method, path) in routes()


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v2/push/request"),
    ("GET", f"/api/v2/patient/{uuid.uuid4()}/summary"),
    ("GET", f"/api/v2/pipeline/jobs/{uuid.uuid4()}"),
    ("POST", "/api/v2/pipeline/documents/upload"),
])
def test_provider_surfaces_require_authentication(test_client, method, path):
    response = test_client.request(method, path)
    assert response.status_code == 401


@pytest.mark.parametrize("path", [
    "/api/v2/patient/not-a-uuid/summary",
    "/api/v2/pipeline/jobs/not-a-uuid",
    "/api/v2/pipeline/fields/not-a-uuid/review",
    "/api/v2/pipeline/jobs/not-a-uuid/commit",
])
def test_identifiers_fail_closed(path, test_client, admin_headers):
    response = test_client.request("POST" if path.endswith(("review", "commit")) else "GET", path, headers=admin_headers, json={})
    assert response.status_code in {401, 403, 422}


def test_legacy_decision_contract_is_not_registered():
    assert ("POST", "/api/v2/push/{request_id}/respond") not in routes()


def test_transparency_mock_contract_is_not_registered():
    assert not any(path.startswith("/api/v2/transparency") for _, path in routes())
