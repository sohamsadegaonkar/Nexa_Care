"""Test-only compatibility bridge for pre-Slice-6B patient harnesses.

Several downstream consent/device tests intentionally bypass patient authentication
by overriding ``get_scoped_session`` so they can focus on cryptography, Redis state,
and route behavior. Slice 6B moved device enrollment to the stronger
``get_current_patient_session`` dependency. These older harnesses should not force a
production compatibility bypass, so this pytest plugin adapts only the named legacy
test files.

When a target harness already supplies a ``get_scoped_session`` override, the bridge
wraps that synthetic patient id in an ``AuthenticatedPatientSession``. When no such
override exists, it delegates to the real strict dependency, preserving all normal
6B authority behavior. Dedicated 6B tests separately qualify live Redis session
creation, revocation, epoch invalidation, and DB identity revalidation.

Security note: this plugin is loaded by pytest before ``tests/conftest.py``. It must
not import ``app.main`` at module import time, because conftest establishes the
restricted test ``TRUSTED_HOSTS`` value before creating the FastAPI app. The app is
therefore imported lazily inside the fixture.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedPatientSession,
    get_current_patient_session,
    get_scoped_session,
)

_LEGACY_SESSION_HARNESS_FILES = {
    "test_device_consent.py",
    "test_device_consent_qa.py",
    "test_consent_flow_qa.py",
    "test_forged_signature.py",
}


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


@pytest.fixture(autouse=True)
def _bridge_legacy_scoped_patient_override(request):
    """Adapt only legacy downstream harnesses; never weaken production deps."""

    if request.node.path.name not in _LEGACY_SESSION_HARNESS_FILES:
        yield
        return

    # Import only after tests/conftest.py has established test environment.
    from app.main import app

    previous = app.dependency_overrides.get(get_current_patient_session)

    async def _current_session_override(
        authorization: str | None = Header(default=None),
        db: AsyncSession = Depends(get_db_session),
    ):
        scoped_override = app.dependency_overrides.get(get_scoped_session)
        if scoped_override is not None:
            patient_id = await _maybe_await(scoped_override())
            if not isinstance(patient_id, str) or not patient_id:
                raise AssertionError("legacy scoped-session override returned no patient id")
            yield AuthenticatedPatientSession(
                patient_id=patient_id,
                patient=SimpleNamespace(patient_uuid=patient_id, is_deleted=False),
                session_id="test-current-patient-session",
                session_epoch=0,
                supabase_user_id="test-supabase-subject",
            )
            return

        original = get_current_patient_session(
            authorization=authorization,
            db=db,
        )
        try:
            yield await original.__anext__()
        finally:
            await original.aclose()

    app.dependency_overrides[get_current_patient_session] = _current_session_override
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_patient_session, None)
        else:
            app.dependency_overrides[get_current_patient_session] = previous


@pytest.fixture(autouse=True)
def _isolate_legacy_registration_retry_from_new_session_store(request):
    """Keep the legacy retry test focused on enrollment-grant recovery semantics."""

    if request.node.name != "test_same_finalized_attempt_recovers_after_device_redis_failure":
        yield
        return

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    issue = AsyncMock(
        side_effect=[
            ("patient-token-1", expires_at, "test-registration-session-1"),
            ("patient-token-2", expires_at, "test-registration-session-2"),
        ]
    )
    revoke = AsyncMock(return_value=True)
    with (
        patch("app.api.v2.auth_routes.issue_patient_access_session", new=issue),
        patch("app.api.v2.auth_routes.revoke_patient_session", new=revoke),
    ):
        yield
