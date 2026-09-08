"""Unit tests for strict current patient authentication.

Verifies the complete runtime chain:
- JWT transport and claims are valid.
- A matching live Redis patient session exists.
- Current Patient and PatientAuthIdentity state remains valid.
- Audit tenant scope is bound only after the full chain succeeds.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.dependencies import AuthenticatedPatient, get_current_patient
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.services.patient_auth_service import issue_patient_access_token
from app.services.patient_session_authority import PatientSessionAuthorityUnavailable

SESSION_ID = "patient-self-session-1234567890"
SESSION_EPOCH = 0


async def _resolve_current_patient(*, authorization, db):
    """Resolve and close the yield dependency for ordinary auth assertions."""
    dependency = get_current_patient(authorization=authorization, db=db)
    try:
        return await dependency.__anext__()
    finally:
        await dependency.aclose()


def _mock_db_with_patient_and_identity(
    patient: Patient | None,
    identity: PatientAuthIdentity | None,
) -> AsyncMock:
    """Create a mock AsyncSession returning the given patient and identity."""
    db = AsyncMock()

    async def _execute(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM patients" in stmt_str or "patients.patient_uuid" in stmt_str:
            mock_result.scalar_one_or_none.return_value = patient
        elif (
            "FROM patient_auth_identities" in stmt_str
            or "patient_auth_identities.patient_id" in stmt_str
        ):
            mock_result.scalar_one_or_none.return_value = identity
        else:
            mock_result.scalar_one_or_none.return_value = None
        return mock_result

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _issue_session_token(patient_id: str, subject: str) -> str:
    token, _ = issue_patient_access_token(
        patient_id,
        subject,
        session_id=SESSION_ID,
        session_epoch=SESSION_EPOCH,
    )
    return token


def _live_session_authority():
    return patch(
        "app.core.dependencies.resolve_patient_session_authority",
        new=AsyncMock(
            return_value={
                "status": "active",
                "session_epoch": SESSION_EPOCH,
            }
        ),
    )


@pytest.mark.asyncio
async def test_missing_authorization_header_returns_401():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=None, db=db)
    assert exc_info.value.status_code == 401
    assert "Missing authorization token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_malformed_token_returns_401():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization="Bearer not.a.valid.jwt", db=db)
    assert exc_info.value.status_code == 401
    assert "Invalid or expired patient token" in exc_info.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "detail"),
    [
        ("raw.jwt.token", "Invalid authorization scheme"),
        ("Basic credentials", "Invalid authorization scheme"),
        ("Token token", "Invalid authorization scheme"),
        ("Bearer", "Invalid authorization scheme"),
        ("Bearer ", "Invalid authorization scheme"),
        ("", "Missing authorization token"),
    ],
)
async def test_patient_self_dependency_rejects_non_bearer_or_empty_authorization(
    authorization, detail
):
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=authorization, db=AsyncMock())
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == detail


@pytest.mark.asyncio
async def test_wrong_actor_type_returns_401(monkeypatch):
    import jwt
    from app.services.patient_auth_service import _jwt_secret

    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {
        "sub": str(uuid.uuid4()),
        "actor_type": "provider",
        "patient_id": str(uuid.uuid4()),
        "supabase_user_id": "sp_user_1",
        "auth_method": "phone_otp",
        "iat": now,
        "exp": now + 900,
        "sid": SESSION_ID,
        "session_epoch": SESSION_EPOCH,
    }
    token = jwt.encode(claims, _jwt_secret(), algorithm="HS256")
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_sub_patient_id_mismatch_returns_401(monkeypatch):
    import jwt
    from app.services.patient_auth_service import _jwt_secret

    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {
        "sub": str(uuid.uuid4()),
        "actor_type": "patient",
        "patient_id": str(uuid.uuid4()),
        "supabase_user_id": "sp_user_1",
        "auth_method": "phone_otp",
        "iat": now,
        "exp": now + 900,
        "sid": SESSION_ID,
        "session_epoch": SESSION_EPOCH,
    }
    token = jwt.encode(claims, _jwt_secret(), algorithm="HS256")
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_non_uuid_patient_id_returns_401(monkeypatch):
    import jwt
    from app.services.patient_auth_service import _jwt_secret

    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {
        "sub": "not-a-uuid",
        "actor_type": "patient",
        "patient_id": "not-a-uuid",
        "supabase_user_id": "sp_user_1",
        "auth_method": "phone_otp",
        "iat": now,
        "exp": now + 900,
        "sid": SESSION_ID,
        "session_epoch": SESSION_EPOCH,
    }
    token = jwt.encode(claims, _jwt_secret(), algorithm="HS256")
    db = AsyncMock()
    with _live_session_authority(), pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401
    assert "Invalid patient identity" in exc_info.value.detail


@pytest.mark.asyncio
async def test_missing_live_session_rejects_valid_jwt_before_database(monkeypatch):
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    token = _issue_session_token(str(uuid.uuid4()), "sp_user_1")
    db = AsyncMock()
    with (
        patch(
            "app.core.dependencies.resolve_patient_session_authority",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _resolve_current_patient(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Patient session is no longer active"
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_authority_backend_failure_fails_closed(monkeypatch):
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    token = _issue_session_token(str(uuid.uuid4()), "sp_user_1")
    with (
        patch(
            "app.core.dependencies.resolve_patient_session_authority",
            new=AsyncMock(
                side_effect=PatientSessionAuthorityUnavailable("redis unavailable")
            ),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _resolve_current_patient(authorization=f"Bearer {token}", db=AsyncMock())
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error_code": "PATIENT_SESSION_AUTHORITY_UNAVAILABLE",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_missing_patient_in_db_returns_401(monkeypatch):
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    pid = str(uuid.uuid4())
    token = _issue_session_token(pid, "sp_user_1")
    db = _mock_db_with_patient_and_identity(patient=None, identity=None)
    with _live_session_authority(), pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401
    assert "Patient account unavailable" in exc_info.value.detail


@pytest.mark.asyncio
async def test_deleted_patient_in_db_returns_401(monkeypatch):
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    pid = uuid.uuid4()
    token = _issue_session_token(str(pid), "sp_user_1")
    patient = Patient(patient_uuid=pid, is_deleted=True)
    db = _mock_db_with_patient_and_identity(patient=patient, identity=None)
    with _live_session_authority(), pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401
    assert "Patient account unavailable" in exc_info.value.detail


@pytest.mark.asyncio
async def test_missing_patient_auth_identity_returns_401(monkeypatch):
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    pid = uuid.uuid4()
    token = _issue_session_token(str(pid), "sp_user_1")
    patient = Patient(patient_uuid=pid, is_deleted=False)
    db = _mock_db_with_patient_and_identity(patient=patient, identity=None)
    with _live_session_authority(), pytest.raises(HTTPException) as exc_info:
        await _resolve_current_patient(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401
    assert "Patient identity not verified" in exc_info.value.detail


@pytest.mark.asyncio
async def test_valid_patient_jwt_resolves_authenticated_patient(monkeypatch):
    from app.security.audit_context import current_audit_context, AuditDomain

    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    pid = uuid.uuid4()
    token = _issue_session_token(str(pid), "sp_user_1")
    patient = Patient(patient_uuid=pid, is_deleted=False)
    identity = PatientAuthIdentity(
        patient_id=pid,
        provider="supabase",
        provider_subject="sp_user_1",
        revoked_at=None,
    )
    db = _mock_db_with_patient_and_identity(patient=patient, identity=identity)

    with _live_session_authority():
        dependency = get_current_patient(authorization=f"Bearer {token}", db=db)
        auth = await dependency.__anext__()
        assert isinstance(auth, AuthenticatedPatient)
        assert auth.patient_id == str(pid)
        assert auth.patient == patient
        audit_ctx = current_audit_context(AuditDomain.PATIENT_RECORD)
        assert audit_ctx.tenant_id == str(pid)
        await dependency.aclose()

    assert current_audit_context(AuditDomain.PATIENT_RECORD).tenant_id == "test-tenant"


@pytest.mark.asyncio
async def test_patient_self_dependency_restores_prior_audit_scope_after_exception(
    monkeypatch,
):
    from app.security.audit_context import (
        AuditDomain,
        bind_trusted_audit_hospital,
        current_audit_context,
        reset_trusted_audit_scope,
    )

    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    pid = uuid.uuid4()
    token = _issue_session_token(str(pid), "sp_user_1")
    patient = Patient(patient_uuid=pid, is_deleted=False)
    identity = PatientAuthIdentity(
        patient_id=pid,
        provider="supabase",
        provider_subject="sp_user_1",
        revoked_at=None,
    )
    db = _mock_db_with_patient_and_identity(patient=patient, identity=identity)
    prior_token = bind_trusted_audit_hospital("prior-hospital")
    with _live_session_authority():
        dependency = get_current_patient(authorization=f"bearer {token}", db=db)
        try:
            auth = await dependency.__anext__()
            assert auth.patient_id == str(pid)
            assert current_audit_context(AuditDomain.PATIENT_RECORD).tenant_id == str(pid)
            await dependency.athrow(RuntimeError("route failure"))
        except RuntimeError as exc:
            assert str(exc) == "route failure"
        finally:
            await dependency.aclose()

    restored = current_audit_context(AuditDomain.PATIENT_RECORD)
    assert restored.hospital_id == "prior-hospital"
    reset_trusted_audit_scope(prior_token)


@pytest.mark.asyncio
async def test_patient_self_dependency_does_not_leave_scope_between_requests(
    monkeypatch,
):
    from app.security.audit_context import AuditDomain, current_audit_context

    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )
    patient_ids = [uuid.uuid4(), uuid.uuid4()]
    with _live_session_authority():
        for index, pid in enumerate(patient_ids):
            token = _issue_session_token(str(pid), f"sp_user_{index}")
            db = _mock_db_with_patient_and_identity(
                patient=Patient(patient_uuid=pid, is_deleted=False),
                identity=PatientAuthIdentity(
                    patient_id=pid,
                    provider="supabase",
                    provider_subject=f"sp_user_{index}",
                    revoked_at=None,
                ),
            )
            dependency = get_current_patient(authorization=f"Bearer {token}", db=db)
            try:
                await dependency.__anext__()
                assert current_audit_context(AuditDomain.PATIENT_RECORD).tenant_id == str(
                    pid
                )
            finally:
                await dependency.aclose()
            assert (
                current_audit_context(AuditDomain.PATIENT_RECORD).tenant_id
                == "test-tenant"
            )


@pytest.mark.asyncio
async def test_unauthenticated_request_has_no_patient_scope_in_a_blank_context():
    from app.security.audit_context import (
        AuditContextMissing,
        AuditDomain,
        current_audit_context,
    )

    async def _assert_no_scope() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_current_patient(authorization=None, db=AsyncMock())
        assert exc_info.value.status_code == 401
        with pytest.raises(AuditContextMissing):
            current_audit_context(AuditDomain.PATIENT_RECORD)

    task = contextvars.Context().run(asyncio.create_task, _assert_no_scope())
    await task
