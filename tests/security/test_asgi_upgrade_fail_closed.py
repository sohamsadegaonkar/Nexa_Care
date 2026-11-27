"""Request-boundary regressions for the FastAPI/Starlette security upgrade.

These hit Nexa Care's *registered* routes and middleware using synthetic
requests. They do not replace the PostgreSQL/Redis signed-consent,
cross-hospital and encryption qualification suites.
"""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("application/pdf", b"%PDF-1.4\n% adversarial untrusted sample\n"),
        ("image/png", b"not-a-real-png"),
    ],
)
def test_anonymous_multipart_upload_rejected_before_processing(
    test_client, content_type: str, body: bytes
) -> None:
    """A syntactically valid multipart request must not bypass provider auth."""
    response = test_client.post(
        "/api/v2/pipeline/documents/upload",
        files={"file": ("untrusted-upload", body, content_type)},
    )
    assert response.status_code == 401
    assert "untrusted-upload" not in response.text


def test_forged_cookie_and_cross_site_origin_cannot_upload(test_client) -> None:
    """Cookie authentication requires valid session + CSRF; not just cookie syntax."""
    response = test_client.post(
        "/api/v2/pipeline/documents/upload",
        files={"file": ("attack.pdf", b"%PDF-1.4\n", "application/pdf")},
        cookies={"nexa_provider_session": "forged-session"},
        headers={"Origin": "https://malicious.invalid"},
    )
    assert response.status_code in {401, 403}
    assert "forged-session" not in response.text


def test_bearer_token_is_not_reflected_in_exception_response(test_client) -> None:
    """Invalid bearer authentication is denied without disclosing token bytes."""
    sentinel = "adversarial-invalid-bearer-token"
    response = test_client.get(
        f"/api/v2/patient/{uuid4()}/summary",
        headers={"Authorization": f"Bearer {sentinel}"},
    )
    assert response.status_code in {401, 403}
    assert sentinel not in response.text
    assert sentinel not in str(response.headers)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/consent/request",
        "/api/v2/pipeline/documents/upload",
    ],
)
def test_untrusted_host_cannot_reach_sensitive_routes(test_client, path: str) -> None:
    """Trusted-host middleware must reject attacker-controlled host headers."""
    response = test_client.post(path, headers={"Host": "malicious.invalid"})
    assert response.status_code == 400
