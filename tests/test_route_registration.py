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


# Reconciled 2026-07-06 against the actual router registrations in
# app/main.py after a full audit of a 16-test CI failure (see the PR that
# added this comment for the full breakdown). Two things changed since this
# set was last updated:
#
#   1. The old /api/v2/assurance/push/request and
#      /api/v2/assurance/biometric/verify endpoints were retired when the
#      notification rework replaced them with the Expo-push + signed-response
#      design now living under /api/v2/push/* (assurance_routes.py). There is
#      no standalone biometric/verify route any more -- verification is
#      folded into POST /api/v2/push/{request_id}/respond.
#   2. Several routers shipped after this set was last touched and were
#      never added: merge-challenge auth (auth_routes.py), break-glass
#      revoke (consent_routes.py), consent validation (consent_routes.py),
#      and cryptographic erasure (patient_routes.py).
#
# If this file goes red again: don't just delete the offending entries to
# make it pass. Confirm with whoever owns the route in question whether the
# route was intentionally added/removed, then update this set with a note
# like this one so the next drift has a breadcrumb instead of a guess.
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
    ("POST", "/api/v2/auth/login"),
    ("POST", "/api/v2/auth/logout"),
    ("POST", "/api/v2/auth/refresh"),
    ("POST", "/api/v2/auth/mfa/setup"),
    ("POST", "/api/v2/auth/mfa/setup/verify"),
    ("POST", "/api/v2/auth/mfa/verify"),
    ("POST", "/api/v2/auth/mfa/verify-action"),
    ("GET", "/api/v2/auth/me/role"),
    ("POST", "/api/v2/auth/challenge/merge"),
    ("POST", "/api/v2/auth/challenge/merge/verify"),
    ("POST", "/api/v2/push/request"),
    ("POST", "/api/v2/push/{request_id}/respond"),
    ("GET", "/api/v2/push/{request_id}/status"),
    ("POST", "/api/v2/push/register-token"),
    ("POST", "/api/v2/push/register-device-key"),
    ("GET", "/api/v2/push/transport-config"),
    ("POST", "/api/v2/consent/grant"),
    ("GET", "/api/v2/consent/history"),
    ("GET", "/api/v2/consent/validate"),
    ("POST", "/api/v2/consent/routine/issue"),
    ("POST", "/api/v2/consent/break-glass/issue"),
    ("POST", "/api/v2/consent/break-glass/revoke"),
    ("POST", "/api/v2/nfc/resolve"),
    ("GET", "/api/v2/fhir/export/{patient_id}"),
    ("GET", "/api/v2/patient/{patient_id}/record"),
    ("POST", "/api/v2/patient/merge"),
    ("POST", "/api/v2/patient/{patient_id}/erase"),
    ("POST", "/api/v2/patient/merge"),
    ("GET", "/api/v2/patient/{patient_uuid}/policy"),
    ("PUT", "/api/v2/patient/{patient_uuid}/policy"),
    ("GET", "/api/v2/dashboard/metrics"),
    ("POST", "/api/v2/reviews/{review_id}/reject"),
    ("POST", "/api/v2/reviews/{review_id}/approve"),
    ("GET", "/api/v2/reviews/pending"),
    ("POST", "/api/v2/patient/devices/enroll"),
    ("GET", "/api/v2/patient/devices"),
    ("POST", "/api/v2/patient/devices/{device_id}/revoke"),
    ("POST", "/api/v2/consent/request"),
    ("POST", "/api/v2/consent/approve-signed"),
    ("GET", "/api/v2/consent/status/{request_id}"),
    ("POST", "/api/v2/consent/request/{request_id}/cancel"),  # Day 14: real server-side cancellation
    ("GET", "/api/v2/consent/challenge/{request_id}"),
    ("GET", "/api/v2/patient/{id}/summary"),
    ("GET", "/api/v2/patient/{id}/timeline"),
    ("GET", "/api/v2/patient/me/timeline"),
    ("GET", "/api/v2/patient/me/access-history"),
    ("GET", "/api/v2/patient/{id}/audit-trail"),
    ("GET", "/api/v2/patient/{id}/records"),
    ("GET", "/api/v2/patient/{id}/structured-record"),
    ("POST", "/api/v2/patient/{id}/record/vitals"),
    ("POST", "/api/v2/patient/{id}/records/vitals"),
    ("POST", "/api/v2/patient/{id}/record/medications"),
    ("POST", "/api/v2/patient/{id}/records/medications"),
    ("POST", "/api/v2/patient/{id}/record/labs"),
    ("POST", "/api/v2/patient/{id}/records/labs"),
    ("POST", "/api/v2/patient/{id}/record/allergies"),
    ("POST", "/api/v2/patient/{id}/records/allergies"),
    ("POST", "/api/v2/patient/{id}/record/documents"),
    ("POST", "/api/v2/patient/{id}/records/documents"),
    ("POST", "/api/v2/pipeline/documents/upload"),
    ("GET", "/api/v2/pipeline/jobs/{job_id}"),
    ("GET", "/api/v2/pipeline/review-queue"),
    ("POST", "/api/v2/pipeline/fields/{field_id}/review"),
    ("POST", "/api/v2/pipeline/fields/{field_id}/approve"),
    ("POST", "/api/v2/pipeline/fields/{field_id}/reject"),
    ("POST", "/api/v2/pipeline/fields/{field_id}/edit"),
    ("POST", "/api/v2/pipeline/jobs/{job_id}/commit"),
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