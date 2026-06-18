"""
Regression tests for the handshake/consent scoping fix: a session minted
for one patient must never authorize access to a different patient via
either GET /api/v1/record/{id} or POST /request-consent.

session_authorizes_patient is a pure function (dict in, bool out), so this
needs no app, no Redis, no Supabase -- exactly the kind of cheap test that
should exist for every security-relevant decision point.
"""
from app.services.auth_service import session_authorizes_patient

PATIENT_A = "11111111-1111-1111-1111-111111111111"
PATIENT_B = "22222222-2222-2222-2222-222222222222"


def test_no_session_is_never_authorized():
    assert session_authorizes_patient(None, PATIENT_A) is False


def test_empty_session_is_never_authorized():
    assert session_authorizes_patient({}, PATIENT_A) is False


def test_session_for_one_patient_does_not_authorize_a_different_patient():
    session = {"authenticated": True, "masked_internal_id": PATIENT_A}
    assert session_authorizes_patient(session, PATIENT_B) is False


def test_session_authorizes_its_own_patient():
    session = {"authenticated": True, "masked_internal_id": PATIENT_A}
    assert session_authorizes_patient(session, PATIENT_A) is True


def test_legacy_session_without_scope_fails_closed():
    """
    Sessions minted before this fix shipped won't carry masked_internal_id
    at all. They must be rejected, not treated as a wildcard match -- this
    is the exact bug being fixed, so it has to fail closed during rollout
    too, not just going forward.
    """
    legacy_session = {"authenticated": True, "nfc_uid": "some-device"}
    assert session_authorizes_patient(legacy_session, PATIENT_A) is False


def test_uuid_formatting_differences_still_match():
    # Stored value is always canonicalized via str(uuid.UUID(...)); the
    # requested id might arrive in a different case from a client.
    session = {"masked_internal_id": PATIENT_A}
    assert session_authorizes_patient(session, PATIENT_A.upper()) is True