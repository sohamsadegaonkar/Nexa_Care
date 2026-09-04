"""Unit tests for ProviderTrustPermissionApplicationService."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import app.services.provider_trust_permission_application as app_module
from app.models.provider import (
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.provider_trust_authorization import TrustManagementAuthentication
from app.services.provider_trust_permission_application import (
    ProviderTrustPermissionApplicationError,
    ProviderTrustPermissionApplicationService,
    _canonical_dt,
    _request_hash_grant,
    _request_hash_revoke,
)
from app.services.provider_trust_permission_policy import (
    RevocationReasonCode,
)


NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def _valid_auth(
    actor_id: UUID, mfa_time: datetime | None = None
) -> TrustManagementAuthentication:
    return TrustManagementAuthentication(
        provider_id=actor_id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=mfa_time or NOW,
    )


def _active_identity(
    provider_id: UUID, is_active: bool = True, status: str = "active"
) -> ProviderIdentity:
    identity = ProviderIdentity(
        id=provider_id,
        provider_uid=f"uid-{provider_id.hex[:8]}",
        contact_email=f"prov-{provider_id.hex[:8]}@example.test",
        contact_phone="+919876543210",
        email_verified_at=NOW,
        phone_verified_at=NOW,
        status=status,
        is_active=is_active,
    )
    identity.credential = ProviderCredential(
        provider_id=provider_id,
        login_identifier=f"login-{provider_id.hex[:8]}",
        password_hash="argon2-hash",
        mfa_secret="mfa-secret",
        mfa_enabled=True,
        is_active=is_active,
    )
    return identity


def _root_grant(
    actor_id: UUID,
    valid_until: datetime | None = None,
    revoked_at: datetime | None = None,
) -> ProviderTrustPermissionGrant:
    return ProviderTrustPermissionGrant(
        id=uuid4(),
        provider_id=actor_id,
        permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
        scope_type=TrustPermissionScope.GLOBAL.value,
        facility_id=None,
        granted_at=NOW - timedelta(days=30),
        valid_from=NOW - timedelta(days=30),
        valid_until=valid_until,
        revoked_at=revoked_at,
        granted_by_actor_id=str(uuid4()),
        governance_reference="INIT-ROOT",
    )


def test_canonical_grant_request_hashing():
    actor = uuid4()
    target = uuid4()
    facility = uuid4()

    h1 = _request_hash_grant(
        actor_id=actor,
        target_provider_id=target,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        scope_type=TrustPermissionScope.FACILITY,
        facility_id=facility,
        valid_from=NOW,
        valid_until=NOW + timedelta(days=30),
        governance_reference="REF-1",
    )
    # Identical semantic request produces identical hash
    h2 = _request_hash_grant(
        actor_id=actor,
        target_provider_id=target,
        permission=TrustManagementPermission.FACILITY_REVIEW,
        scope_type=TrustPermissionScope.FACILITY,
        facility_id=facility,
        valid_from=NOW,
        valid_until=NOW + timedelta(days=30),
        governance_reference="  REF-1  ",
    )
    assert h1 == h2

    # Different permission produces different hash
    h3 = _request_hash_grant(
        actor_id=actor,
        target_provider_id=target,
        permission=TrustManagementPermission.AFFILIATION_MANAGE,
        scope_type=TrustPermissionScope.FACILITY,
        facility_id=facility,
        valid_from=NOW,
        valid_until=NOW + timedelta(days=30),
        governance_reference="REF-1",
    )
    assert h1 != h3


def test_canonical_revoke_request_hashing():
    actor = uuid4()
    grant_id = uuid4()

    h1 = _request_hash_revoke(
        actor_id=actor,
        grant_id=grant_id,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
        governance_reference="REF-REV",
    )
    h2 = _request_hash_revoke(
        actor_id=actor,
        grant_id=grant_id,
        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
        governance_reference="  REF-REV  ",
    )
    assert h1 == h2

    h3 = _request_hash_revoke(
        actor_id=actor,
        grant_id=grant_id,
        revocation_reason_code=RevocationReasonCode.SECURITY_RESPONSE,
        governance_reference="REF-REV",
    )
    assert h1 != h3


def test_idempotent_replay_success():
    service = ProviderTrustPermissionApplicationService(db=None)  # type: ignore[arg-type]
    actor = uuid4()
    target = uuid4()
    grant_id = uuid4()

    hash_val = _request_hash_grant(
        actor_id=actor,
        target_provider_id=target,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
        scope_type=TrustPermissionScope.GLOBAL,
        facility_id=None,
        valid_from=None,
        valid_until=None,
        governance_reference=None,
    )

    row = type(
        "Row",
        (),
        {
            "request_hash": hash_val,
            "response_status": 200,
            "response_payload": {
                "command": "GRANT",
                "grant_id": str(grant_id),
                "target_provider_id": str(target),
                "permission": "PROFESSIONAL_REVIEW",
                "scope_type": "GLOBAL",
                "facility_id": None,
                "superseded_grant_id": None,
                "event_types": ["PROVIDER_TRUST_PERMISSION_GRANTED"],
            },
        },
    )()

    result = service._replay(
        row, hash_val, "provider.trust.permission.grant.v1", target
    )
    assert result.idempotent_replay is True
    assert result.grant_id == grant_id
    assert result.target_provider_id == target
    assert result.command == "GRANT"


def test_idempotency_conflict_and_in_progress():
    service = ProviderTrustPermissionApplicationService(db=None)  # type: ignore[arg-type]
    target = uuid4()

    row_conflict = type(
        "Row",
        (),
        {
            "request_hash": "different-hash",
            "response_status": 200,
            "response_payload": {},
        },
    )()
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._replay(row_conflict, "my-hash", "op", target)
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"

    row_in_progress = type(
        "Row",
        (),
        {
            "request_hash": "my-hash",
            "response_status": None,
            "response_payload": None,
        },
    )()
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._replay(row_in_progress, "my-hash", "op", target)
    assert exc.value.code == "IDEMPOTENCY_IN_PROGRESS"


def test_step_up_mfa_15_minute_freshness():
    service = ProviderTrustPermissionApplicationService(db=None)  # type: ignore[arg-type]
    actor_id = uuid4()
    actor = _active_identity(actor_id)
    root_grants = [_root_grant(actor_id)]

    # 1. Fresh MFA (now) -> passes
    auth_fresh = _valid_auth(actor_id, mfa_time=NOW)
    service._authorize_actor_authority(actor, root_grants, auth_fresh, NOW)

    # 2. MFA at 14m 59s -> passes
    auth_14m59s = _valid_auth(
        actor_id, mfa_time=NOW - timedelta(minutes=14, seconds=59)
    )
    service._authorize_actor_authority(actor, root_grants, auth_14m59s, NOW)

    # 3. MFA at 15m 01s -> fails with MFA_STEP_UP_REQUIRED
    auth_15m01s = _valid_auth(actor_id, mfa_time=NOW - timedelta(minutes=15, seconds=1))
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._authorize_actor_authority(actor, root_grants, auth_15m01s, NOW)
    assert exc.value.code == "MFA_STEP_UP_REQUIRED"

    # 4. MFA missing or future -> AUTHORIZATION_DENIED
    auth_future = _valid_auth(actor_id, mfa_time=NOW + timedelta(minutes=1))
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._authorize_actor_authority(actor, root_grants, auth_future, NOW)
    assert exc.value.code == "AUTHORIZATION_DENIED"


def test_root_authorization_evaluates_locked_grants():
    service = ProviderTrustPermissionApplicationService(db=None)  # type: ignore[arg-type]
    actor_id = uuid4()
    actor = _active_identity(actor_id)
    auth = _valid_auth(actor_id, mfa_time=NOW)

    # 1. No grants -> AUTHORIZATION_DENIED
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._authorize_actor_authority(actor, [], auth, NOW)
    assert exc.value.code == "AUTHORIZATION_DENIED"

    # 2. Expired root grant -> AUTHORIZATION_DENIED
    expired_grant = _root_grant(actor_id, valid_until=NOW - timedelta(minutes=1))
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._authorize_actor_authority(actor, [expired_grant], auth, NOW)
    assert exc.value.code == "AUTHORIZATION_DENIED"

    # 3. Revoked root grant -> AUTHORIZATION_DENIED
    revoked_grant = _root_grant(actor_id, revoked_at=NOW - timedelta(minutes=1))
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._authorize_actor_authority(actor, [revoked_grant], auth, NOW)
    assert exc.value.code == "AUTHORIZATION_DENIED"

    # 4. Non-root grant only (e.g. PROFESSIONAL_REVIEW) -> AUTHORIZATION_DENIED
    prof_grant = ProviderTrustPermissionGrant(
        id=uuid4(),
        provider_id=actor_id,
        permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,
        scope_type=TrustPermissionScope.GLOBAL.value,
        facility_id=None,
        granted_at=NOW - timedelta(days=1),
        valid_from=NOW - timedelta(days=1),
        valid_until=None,
        revoked_at=None,
        granted_by_actor_id=str(uuid4()),
    )
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        service._authorize_actor_authority(actor, [prof_grant], auth, NOW)
    assert exc.value.code == "AUTHORIZATION_DENIED"


def test_root_permission_grant_denied_offline_only():
    actor_id = uuid4()
    target_id = uuid4()
    service = ProviderTrustPermissionApplicationService(db=None)  # type: ignore[arg-type]
    auth = _valid_auth(actor_id)

    import asyncio

    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        asyncio.run(
            service.apply_grant(
                actor_id=actor_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE,
                idempotency_key="key-12345",
                now=NOW,
            )
        )
    assert exc.value.code == "TRUST_PERMISSION_POLICY_DENIED"
    assert exc.value.policy_code == "ROOT_PERMISSION_OFFLINE_ONLY"


def test_application_service_architecture_isolation():
    """Verify application service imports zero fastapi, starlette, or redis modules."""
    import ast

    source = inspect.getsource(app_module)
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)

    forbidden_prefixes = ["fastapi", "starlette", "redis"]
    for imported in imported_modules:
        for forbidden in forbidden_prefixes:
            assert not imported.startswith(
                forbidden
            ), f"Forbidden module import '{imported}' found in application service!"


def test_canonical_dt_rejects_naive_datetime():
    naive = datetime(2026, 9, 4, 12, 0, 0)
    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        _canonical_dt(naive)
    assert exc.value.code == "INVALID_REQUEST"


def test_invalid_idempotency_key_format_rejected_before_db():
    actor_id = uuid4()
    target_id = uuid4()
    service = ProviderTrustPermissionApplicationService(db=None)  # type: ignore[arg-type]
    auth = _valid_auth(actor_id)

    import asyncio

    with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
        asyncio.run(
            service.apply_grant(
                actor_id=actor_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key="   ",  # whitespace invalid
                now=NOW,
            )
        )
    assert exc.value.code == "INVALID_REQUEST"


def test_sorted_provider_lock_ordering_logic():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    id_a = UUID("00000000-0000-0000-0000-000000000001")
    id_b = UUID("00000000-0000-0000-0000-000000000002")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    service = ProviderTrustPermissionApplicationService(db=mock_db)

    # Call with B first, then A
    asyncio.run(service._lock_involved_providers({id_b, id_a}))

    # Check execute calls to verify queries lock id_a before id_b
    calls = mock_db.execute.call_args_list
    assert len(calls) == 6  # 3 queries per provider (identity, credential, grants)
    # The first 3 queries are for id_a, the second 3 queries are for id_b
    q1_str = str(calls[0][0][0])
    q4_str = str(calls[3][0][0])
    assert "provider_identity" in q1_str.lower()
    assert "provider_identity" in q4_str.lower()
    # Confirm params passed: first set for id_a, second set for id_b
    assert calls[0][0][0].compile().params["id_1"] == id_a
    assert calls[3][0][0].compile().params["id_1"] == id_b


def test_actor_equals_target_deduped_locking():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    actor_id = UUID("00000000-0000-0000-0000-000000000001")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    service = ProviderTrustPermissionApplicationService(db=mock_db)

    # Actor == Target: passing set with only actor_id
    asyncio.run(service._lock_involved_providers({actor_id, actor_id}))

    calls = mock_db.execute.call_args_list
    # Must only execute 3 queries (not 6)
    assert len(calls) == 3
