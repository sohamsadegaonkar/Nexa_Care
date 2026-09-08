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
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedPatientSession,
    get_current_patient_session,
    get_scoped_session,
)
from app.main import app

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
