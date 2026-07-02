"""
Regression test for the duplicate route definitions that used to exist
for /api/v1/handshake, /api/v1/record/{id}, and /register.

FastAPI/Starlette never errors on a duplicate (method, path) registration
-- it silently lets the first one win and the rest become unreachable
dead code. That's exactly how this slipped in unnoticed, and exactly why
it needs an explicit check rather than relying on the app "working" in
manual testing (the live routes still respond fine; it's the *other*
copy, with different behavior, that you'd never notice was dead).

EXPECTED_ROUTES fix: this previously listed
("GET", "/api/v1/record/{masked_internal_id}") and never listed
("POST", "/api/v1/enroll-biometric") at all. Both were stale against the
actual app:

  - GET /api/v1/record takes its patient id ONLY from the caller's
    handshake session (Depends(get_scoped_session) in
    app/api/routes.py) -- never from a URL path parameter. That path
    param was the exact IDOR this dependency was introduced to close, so
    asserting it should still exist was asserting the *vulnerable* shape.
  - POST /api/v1/enroll-biometric is a real, provider-gated route that
    was simply never added to this set when it shipped.

Left as it was, this file was a guaranteed-red (or silently-ignored) CI
check: every run would report the fixed route as "missing" and the real
route as "unexpected," which is worse than having no test at all --
a false positive here teaches the team to ignore this file's failures,
which is exactly when a *real* duplicate-route regression would slip
through unnoticed too.

No mocking needed: this only inspects the real `app.routes` list that
gets built at import time from the route decorators in app/api/routes.py.
"""
from app.main import app


EXPECTED_ROUTES = {
    ("POST", "/api/v1/handshake"),
    ("POST", "/api/v1/enroll-biometric"),
    ("GET", "/api/v1/record"),
    ("POST", "/register"),
    ("POST", "/request-consent"),
    ("GET", "/view-record/clinical"),
    ("GET", "/view-record/pii"),
    ("POST", "/api/v1/process-document"),
    ("POST", "/api/v2/emergency/read-card"),
    ("POST", "/api/v2/documents/upload"),
    ("POST", "/api/v2/auth/login"),
    ("POST", "/api/v2/consent/grant"),
    ("GET", "/api/v2/fhir/export/{patient_id}"),
    ("GET", "/api/v2/patient/{patient_id}/record"),
    ("POST", "/api/v2/reviews/{review_id}/reject"),
    ("POST", "/api/v2/reviews/{review_id}/approve"),
    ("GET", "/api/v2/reviews/pending"),
    ("GET", "/health"),
    # FastAPI auto-generates these documentation routes
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/openapi.json"),
}


def _registered_routes():
    found = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            if method == "HEAD":  # FastAPI adds this automatically alongside GET
                continue
            found.append((method, path))
    return found


def test_no_duplicate_route_registrations():
    registered = _registered_routes()

    counts: dict[tuple[str, str], int] = {}
    for key in registered:
        counts[key] = counts.get(key, 0) + 1

    duplicates = {key: count for key, count in counts.items() if count > 1}
    assert not duplicates, f"Duplicate route registrations found: {duplicates}"


def test_expected_routes_are_registered():
    registered = set(_registered_routes())
    missing = EXPECTED_ROUTES - registered
    assert not missing, f"Expected routes missing from the app: {missing}"


def test_no_unexpected_extra_routes():
    """
    Catches the inverse case too: a route existing that isn't in
    EXPECTED_ROUTES is worth a second look (e.g. a debug endpoint that
    shouldn't have shipped) rather than being silently fine.
    """
    registered = set(_registered_routes())
    extra = registered - EXPECTED_ROUTES
    assert not extra, f"Unexpected routes registered (update EXPECTED_ROUTES if intentional): {extra}"