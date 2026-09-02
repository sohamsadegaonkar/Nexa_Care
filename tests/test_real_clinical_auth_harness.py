"""Real provider-session and clinical-trust route harness."""

import asyncio
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

import app.core.dependencies as dependencies
from app.core.database import get_db_session
from app.core.dependencies import get_current_provider, get_provider_context
from app.main import app
from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerificationStatus,
    ProfessionalVerificationStatus,
)
from app.security.provider_capabilities import ClinicalCapability
from tests.helpers.clinical_auth_harness import (
    ClinicalSessionFactory,
    ClinicalTestSession,
)


@pytest.fixture
def real_clinical_session(
    mock_db, mock_redis, request
) -> Iterator[ClinicalTestSession]:
    """Install only a session-specific authoritative trust-state adapter."""

    factory_kwargs = getattr(request, "param", {})
    clinical_session = asyncio.run(
        ClinicalSessionFactory(mock_db).create(**factory_kwargs)
    )
    assert any(key.startswith("provider_session:") for key in mock_redis.data)

    previous_db_override = app.dependency_overrides.get(get_db_session)

    async def _clinical_db():
        yield clinical_session.db

    app.dependency_overrides[get_db_session] = _clinical_db
    try:
        yield clinical_session
    finally:
        if previous_db_override is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous_db_override


@pytest.fixture
def record_read_route() -> Iterator[None]:
    """Expose a handler protected exclusively by the production record gate."""

    record_read_dep = dependencies.require_clinical_capability(
        ClinicalCapability.RECORD_READ
    )
    assert get_provider_context not in app.dependency_overrides
    assert get_current_provider not in app.dependency_overrides
    assert record_read_dep not in app.dependency_overrides

    async def clinical_record_read(_=Depends(record_read_dep)):
        return {"ok": True}

    app.add_api_route(
        "/__test/clinical-record-read", clinical_record_read, methods=["GET"]
    )
    route = app.router.routes[-1]
    try:
        yield
    finally:
        app.router.routes.remove(route)


def _request_with_real_auth(headers):
    """Exercise the route while observing, but never replacing, session auth."""

    real_auth = dependencies.authenticate_provider_session
    with patch(
        "app.core.dependencies.authenticate_provider_session",
        wraps=real_auth,
    ) as auth_spy:
        response = TestClient(app).get(
            "/__test/clinical-record-read",
            headers=headers,
        )
    return response, auth_spy


def _assert_generic_eligibility_denial(response) -> None:
    assert response.status_code == 403
    assert response.json() == {"detail": {"error_code": "CLINICAL_ELIGIBILITY_DENIED"}}


def test_verified_session_reaches_real_record_read_gate(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth_spy.await_count >= 1


def test_missing_session_is_rejected_before_clinical_authorization(
    real_clinical_session, record_read_route
):
    response = TestClient(app).get("/__test/clinical-record-read")

    assert response.status_code == 401


def test_user_agent_mismatch_is_rejected_by_real_session_authentication(
    real_clinical_session, record_read_route
):
    headers = dict(real_clinical_session.headers)
    headers["User-Agent"] = "DifferentAgent/1.0"

    response, auth_spy = _request_with_real_auth(headers)

    assert response.status_code == 401
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"mfa_verified": False}],
    indirect=True,
)
def test_missing_session_mfa_evidence_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    assert response.status_code == 428
    assert response.json() == {"detail": {"error_code": "CLINICAL_MFA_REQUIRED"}}
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"mfa_enabled": False}],
    indirect=True,
)
def test_mfa_enrollment_disabled_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    assert response.status_code == 428
    assert response.json() == {
        "detail": {"error_code": "CLINICAL_MFA_ENROLLMENT_REQUIRED"}
    }
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"email_verified": False}],
    indirect=True,
)
def test_email_unverified_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    _assert_generic_eligibility_denial(response)
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"phone_verified": False}],
    indirect=True,
)
def test_phone_unverified_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    _assert_generic_eligibility_denial(response)
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"professional_status": ProfessionalVerificationStatus.SUSPENDED}],
    indirect=True,
)
def test_professional_suspension_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    _assert_generic_eligibility_denial(response)
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"facility_status": FacilityVerificationStatus.SUSPENDED}],
    indirect=True,
)
def test_facility_suspension_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    _assert_generic_eligibility_denial(response)
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"affiliation_status": AffiliationTrustStatus.REVOKED}],
    indirect=True,
)
def test_affiliation_revocation_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    _assert_generic_eligibility_denial(response)
    assert auth_spy.await_count >= 1


@pytest.mark.parametrize(
    "real_clinical_session",
    [{"roles": ("clinical_reviewer",)}],
    indirect=True,
)
def test_absent_record_read_capability_is_denied_by_real_eligibility(
    real_clinical_session, record_read_route
):
    response, auth_spy = _request_with_real_auth(real_clinical_session.headers)

    _assert_generic_eligibility_denial(response)
    assert auth_spy.await_count >= 1
