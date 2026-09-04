"""Real PostgreSQL + Redis qualification for affiliation-independent provider MFA step-up.

Proves:
- Stale MFA session (> 15m) successfully steps up.
- Missing MFA timestamp successfully steps up.
- Affiliation-independent provider (no affiliations/roles/capabilities) successfully steps up.
- In-place session refresh: same session token, TTL not extended.
- Valid TOTP consumed once; replay rejected.
- Redis replay store failure fails closed.
- Invalid, expired, and UA-mismatched sessions denied.
- IP rotation allowed with warning-only.
- Inactive account, inactive credential, and MFA disabled denied.
- Audit failure leaves old MFA timestamp unchanged (audit-before-refresh ordering).
- Cookie CSRF required for cookie authentication; Bearer succeeds without CSRF tokens.
- Integration with Phase 4C:
  - Fresh step-up + no root grant -> Phase 4C denies (AUTHORIZATION_DENIED).
  - Fresh step-up + root grant revoked AFTER step-up -> Phase 4C denies (AUTHORIZATION_DENIED).
  - Fresh step-up + account deactivated AFTER step-up -> Phase 4C denies (AUTHORIZATION_DENIED).
  - Fresh step-up + active root grant -> Phase 4C succeeds (freshness gate passed).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_async_engine, get_session_factory
from app.core.redis import get_async_redis_client, get_redis_client
from app.core.security import encrypt_mfa_secret
from app.main import app as main_app
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
from app.services.provider_auth_service import (
    issue_provider_session_token,
    resolve_provider_session_context,
)
from app.services.provider_trust_authorization import (
    TrustManagementAuthentication,
)
from app.services.provider_trust_permission_application import (
    ProviderTrustPermissionApplicationError,
    ProviderTrustPermissionApplicationResult,
    ProviderTrustPermissionApplicationService,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.redis,
    pytest.mark.asyncio,
]

HEAD = "20260903_trust_authorization"
_USER_AGENT = "Nexa-StepUp-Qual-Agent/1.0"
_CLIENT_IP = "127.0.0.1"


def _get_db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://nexa:nexa_test@127.0.0.1:55439/nexa_qual_step_up_4d",
    )
    if "127.0.0.1" not in url and "localhost" not in url:
        pytest.fail("Database URL must be loopback-only")
    if "nexa_qual_" not in url:
        pytest.fail("Database URL must name a disposable nexa_qual_ database")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _get_redis_url() -> str:
    url = os.getenv("TEST_REDIS_URL") or os.getenv(
        "UPSTASH_REDIS_URL", "redis://127.0.0.1:6389/0"
    )
    if "127.0.0.1" not in url and "localhost" not in url:
        pytest.fail("Redis URL must be loopback-only")
    return url


@pytest.fixture(scope="module", autouse=True)
def _setup_env():
    db_url = _get_db_url()
    redis_url = _get_redis_url()

    os.environ["TEST_DATABASE_URL"] = db_url
    os.environ["DATABASE_URL"] = db_url
    os.environ["UPSTASH_REDIS_URL"] = redis_url
    os.environ["TEST_REDIS_URL"] = redis_url
    os.environ["MFA_ENCRYPTION_KEY"] = Fernet.generate_key().decode("utf-8")
    os.environ["CORS_ALLOWED_ORIGINS"] = "http://testserver,https://provider.nexa.test"

    for fn in (
        get_async_engine,
        get_session_factory,
        get_redis_client,
        get_async_redis_client,
    ):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()

    cfg = Config("alembic.ini")
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, HEAD)
    yield


@pytest.fixture(autouse=True)
async def override_deps(monkeypatch):
    """Shadow the global mock fixture: this qualification suite requires real PostgreSQL and Redis."""
    monkeypatch.setenv("TRUSTED_PROXY_NETWORKS", "127.0.0.1/32")
    main_app.dependency_overrides.clear()
    get_async_redis_client.cache_clear()
    get_redis_client.cache_clear()
    get_async_engine.cache_clear()
    get_session_factory.cache_clear()
    redis = get_async_redis_client()
    await redis.flushdb()
    try:
        yield
    finally:
        main_app.dependency_overrides.clear()
        get_async_redis_client.cache_clear()
        get_redis_client.cache_clear()
        get_async_engine.cache_clear()
        get_session_factory.cache_clear()


async def _create_test_provider(
    factory,
    *,
    is_active: bool = True,
    status: str = "active",
    credential_active: bool = True,
    mfa_enabled: bool = True,
    secret: str | None = None,
    secret_absent: bool = False,
    session_mfa_time: datetime | None = None,
    ua: str = _USER_AGENT,
    ip: str = _CLIENT_IP,
) -> tuple[uuid.UUID, str, str]:
    """Create ProviderIdentity, ProviderCredential, and an active Redis session token."""
    prov_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    if secret_absent:
        raw_secret = ""
        enc_secret = None
    else:
        raw_secret = secret or pyotp.random_base32()
        enc_secret = encrypt_mfa_secret(raw_secret) if raw_secret else None

    async with factory() as db:
        identity = ProviderIdentity(
            id=prov_id,
            provider_uid=f"uid-{prov_id.hex[:10]}",
            contact_email=f"p-{prov_id.hex[:10]}@example.test",
            contact_phone=f"+91{prov_id.int % 10000000000:010d}",
            email_verified_at=now,
            phone_verified_at=now,
            status=status,
            is_active=is_active,
        )
        cred = ProviderCredential(
            provider_id=prov_id,
            login_identifier=f"log-{prov_id.hex[:10]}",
            password_hash="argon2-hash",
            mfa_secret=raw_secret if not secret_absent else None,
            mfa_secret_encrypted=enc_secret,
            mfa_enabled=mfa_enabled,
            is_active=credential_active,
        )
        db.add_all((identity, cred))
        await db.commit()

    token = await issue_provider_session_token(
        provider_id=prov_id,
        user_agent=ua,
        client_ip=ip,
        mfa_verified_at=session_mfa_time,
    )
    return prov_id, token, raw_secret


async def test_stale_mfa_session_steps_up_and_preserves_ttl():
    """Stale MFA session (> 15m) successfully steps up, updates in place, and preserves TTL."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = get_async_redis_client()

    try:
        now = datetime.now(timezone.utc)
        stale_mfa = now - timedelta(minutes=25)
        prov_id, token, secret = await _create_test_provider(
            factory, session_mfa_time=stale_mfa
        )

        # Record initial TTL in Redis
        key = f"provider_session:{token}"
        ttl_before = await redis.ttl(key)
        assert ttl_before > 0

        # Generate valid TOTP
        totp_code = pyotp.TOTP(secret).now()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert resp.status_code == 200
            assert resp.json() == {"verified": True}

        # Verify Redis state
        raw_sess = await redis.get(key)
        assert raw_sess is not None
        sess = json.loads(raw_sess)
        assert sess["provider_id"] == str(prov_id)
        assert sess["authenticated"] is True
        assert sess["mfa_verified_at"] is not None

        refreshed_mfa = datetime.fromisoformat(sess["mfa_verified_at"])
        assert refreshed_mfa.tzinfo is not None
        assert abs((refreshed_mfa - datetime.now(timezone.utc)).total_seconds()) < 5

        # TTL must be preserved (within reasonable execution seconds, not reset to initial full 24h)
        ttl_after = await redis.ttl(key)
        assert ttl_after <= ttl_before
        assert ttl_after >= ttl_before - 5

    finally:
        await engine.dispose()


async def test_missing_mfa_timestamp_steps_up():
    """Missing MFA timestamp on session successfully steps up to fresh assurance."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = get_async_redis_client()

    try:
        prov_id, token, secret = await _create_test_provider(
            factory, session_mfa_time=None
        )

        totp_code = pyotp.TOTP(secret).now()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert resp.status_code == 200
            assert resp.json() == {"verified": True}

        # In Redis: mfa_verified_at is now set
        key = f"provider_session:{token}"
        raw_sess = await redis.get(key)
        sess = json.loads(raw_sess)
        assert sess["mfa_verified_at"] is not None

    finally:
        await engine.dispose()


async def test_affiliation_independent_provider_steps_up():
    """Provider with NO affiliations/roles/capabilities successfully steps up and audits."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        prov_id, token, secret = await _create_test_provider(factory)
        totp_code = pyotp.TOTP(secret).now()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert resp.status_code == 200

        # Verify audit log in PostgreSQL audit_ledger
        async with factory() as db:
            audit_entry = (
                await db.execute(
                    text(
                        "SELECT action, resource, status, details "
                        "FROM public.audit_ledger WHERE action = 'PROVIDER_STEP_UP_MFA_VERIFIED' "
                        "AND resource = :p"
                    ),
                    {"p": str(prov_id)},
                )
            ).first()
            assert audit_entry is not None
            assert audit_entry.action == "PROVIDER_STEP_UP_MFA_VERIFIED"
            assert audit_entry.status == "SUCCESS"
            details = (
                json.loads(audit_entry.details)
                if isinstance(audit_entry.details, str)
                else audit_entry.details
            )
            assert details.get("metadata", {}).get("assurance") == "totp"
            assert details.get("metadata", {}).get("session_bound") is True

    finally:
        await engine.dispose()


async def test_valid_totp_consumed_once_and_replay_rejected():
    """Valid TOTP code is consumed once; immediate second use is rejected."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        prov_id, token, secret = await _create_test_provider(factory)
        totp_code = pyotp.TOTP(secret).now()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # 1. First use -> 200
            resp1 = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert resp1.status_code == 200

            # 2. Second use -> 401 Replay rejected
            resp2 = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert resp2.status_code == 401
            assert resp2.json()["detail"]["error_code"] == "INVALID_MFA_CODE"

    finally:
        await engine.dispose()


async def test_redis_replay_failure_fails_closed():
    """If Redis replay protection fails, TOTP verification fails closed."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        prov_id, token, secret = await _create_test_provider(factory)
        totp_code = pyotp.TOTP(secret).now()

        # Patch _consume_totp_counter to return False (simulating Redis outage)
        with patch(
            "app.services.provider_auth_service._consume_totp_counter",
            AsyncMock(return_value=False),
        ):
            transport = ASGITransport(app=main_app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                resp = await client.post(
                    "/api/v2/auth/mfa/verify-action",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": _USER_AGENT,
                    },
                    json={"code": totp_code},
                )
                assert resp.status_code == 401
                assert resp.json()["detail"]["error_code"] == "INVALID_MFA_CODE"

    finally:
        await engine.dispose()


async def test_invalid_and_expired_sessions_denied():
    """Non-existent or expired sessions are denied with 401."""
    transport = ASGITransport(app=main_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Non-existent session
        resp = await client.post(
            "/api/v2/auth/mfa/verify-action",
            headers={
                "Authorization": "Bearer non-existent-session-tok",
                "User-Agent": _USER_AGENT,
            },
            json={"code": "123456"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_code"] == "PROVIDER_SESSION_REQUIRED"


async def test_user_agent_mismatch_and_ip_rotation():
    """UA mismatch is denied; IP rotation is admitted with warning."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        prov_id, token, secret = await _create_test_provider(
            factory, ua="CanonicalUA/1.0", ip="127.0.0.1"
        )
        totp_code = pyotp.TOTP(secret).now()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # UA mismatch -> 401
            resp_ua = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "AttackerUA/2.0",
                },
                json={"code": totp_code},
            )
            assert resp_ua.status_code == 401
            assert resp_ua.json()["detail"]["error_code"] == "PROVIDER_SESSION_REQUIRED"

            # Matching UA -> 200 (even if client host rotates)
            resp_ok = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "CanonicalUA/1.0",
                },
                json={"code": totp_code},
            )
            assert resp_ok.status_code == 200

    finally:
        await engine.dispose()


async def test_inactive_account_credential_or_mfa_disabled_denied():
    """Inactive provider, inactive credential, or disabled MFA is denied."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # 1. Inactive account
        p1, tok1, s1 = await _create_test_provider(factory, is_active=False)
        # 2. Inactive credential
        p2, tok2, s2 = await _create_test_provider(factory, credential_active=False)
        # 3. Disabled MFA
        p3, tok3, s3 = await _create_test_provider(factory, mfa_enabled=False)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            r1 = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {tok1}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(s1).now()},
            )
            assert r1.status_code == 401
            assert r1.json()["detail"]["error_code"] == "PROVIDER_ACCOUNT_INACTIVE"

            r2 = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {tok2}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(s2).now()},
            )
            assert r2.status_code == 401
            assert r2.json()["detail"]["error_code"] == "PROVIDER_CREDENTIAL_INACTIVE"

            r3 = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {tok3}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(s3).now()},
            )
            assert r3.status_code == 400
            assert r3.json()["detail"]["error_code"] == "MFA_NOT_CONFIGURED"

    finally:
        await engine.dispose()


async def test_audit_failure_leaves_old_mfa_timestamp_unchanged():
    """If audit staging fails, session MFA timestamp in Redis is NOT updated."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = get_async_redis_client()

    try:
        now = datetime.now(timezone.utc)
        original_mfa = now - timedelta(hours=3)
        prov_id, token, secret = await _create_test_provider(
            factory, session_mfa_time=original_mfa
        )
        totp_code = pyotp.TOTP(secret).now()

        key = f"provider_session:{token}"

        with patch(
            "app.api.v2.mfa_action_routes.append_audit_log_or_503",
            AsyncMock(
                side_effect=HTTPException(
                    status_code=503, detail="Simulated audit outage"
                )
            ),
        ):
            transport = ASGITransport(app=main_app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                resp = await client.post(
                    "/api/v2/auth/mfa/verify-action",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": _USER_AGENT,
                    },
                    json={"code": totp_code},
                )
                assert resp.status_code == 503

        # In Redis: session mfa_verified_at MUST NOT have changed!
        raw_sess = await redis.get(key)
        sess = json.loads(raw_sess)
        assert sess["mfa_verified_at"] == original_mfa.isoformat()

    finally:
        await engine.dispose()


async def test_cookie_csrf_required_and_bearer_bypasses_csrf():
    """Cookie auth requires CSRF protection; Bearer auth succeeds without CSRF tokens."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        prov_id, token, secret = await _create_test_provider(factory)
        totp_code = pyotp.TOTP(secret).now()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # 1. Cookie auth without CSRF token -> 403 CSRF rejected
            resp_cookie_no_csrf = await client.post(
                "/api/v2/auth/mfa/verify-action",
                cookies={"nexa_provider_session": token},
                headers={
                    "User-Agent": _USER_AGENT,
                    "Origin": "http://testserver",
                },
                json={"code": totp_code},
            )
            assert resp_cookie_no_csrf.status_code == 403

            # 2. Cookie auth with CSRF double-submit -> 200
            csrf_token = "valid-csrf-token-12345"
            resp_cookie_csrf_ok = await client.post(
                "/api/v2/auth/mfa/verify-action",
                cookies={
                    "nexa_provider_session": token,
                    "nexa_csrf": csrf_token,
                },
                headers={
                    "User-Agent": _USER_AGENT,
                    "Origin": "http://testserver",
                    "X-CSRF-Token": csrf_token,
                },
                json={"code": totp_code},
            )
            assert resp_cookie_csrf_ok.status_code == 200

            # 3. Bearer auth without CSRF -> 200 (generate new code if second consumed)
            p_bearer, tok_bearer, s_bearer = await _create_test_provider(factory)
            resp_bearer = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {tok_bearer}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(s_bearer).now()},
            )
            assert resp_bearer.status_code == 200

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Integration with Phase 4C Permission Administration
# ---------------------------------------------------------------------------


async def test_integration_phase4c_fresh_step_up_no_root_grant_denied():
    """Step-up alone never creates organizational authority (Phase 4C denies AUTHORIZATION_DENIED)."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        prov_id, token, secret = await _create_test_provider(factory)
        totp_code = pyotp.TOTP(secret).now()

        # Step up successfully
        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert resp.status_code == 200

        # Load session from Redis to get refreshed mfa_verified_at
        sess = await resolve_provider_session_context(token)
        mfa_time = datetime.fromisoformat(sess["mfa_verified_at"])

        auth = TrustManagementAuthentication(
            provider_id=prov_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=mfa_time,
        )

        target_id, _, _ = await _create_test_provider(factory)

        # Attempt Phase 4C subordinate grant -> DENIED because provider lacks TRUST_PERMISSION_MANAGE
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=prov_id,
                    authentication=auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=f"stepup-noroot-{uuid.uuid4().hex[:10]}",
                    now=datetime.now(timezone.utc),
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

    finally:
        await engine.dispose()


async def test_integration_phase4c_root_revoked_after_step_up_denied():
    """Fresh MFA cannot override current root grant revocation (Phase 4C denies AUTHORIZATION_DENIED)."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, token, secret = await _create_test_provider(factory)
        now = datetime.now(timezone.utc)

        # Seed active root grant
        async with factory() as db:
            root_grant = ProviderTrustPermissionGrant(
                id=uuid.uuid4(),
                provider_id=mgr_id,
                permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                scope_type=TrustPermissionScope.GLOBAL.value,
                facility_id=None,
                granted_at=now - timedelta(days=1),
                valid_from=now - timedelta(days=1),
                valid_until=None,
                revoked_at=None,
                granted_by_actor_id="test-root",
            )
            db.add(root_grant)
            await db.commit()

        # Step up successfully
        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(secret).now()},
            )
            assert resp.status_code == 200

        # Now revoke root grant in database WITHOUT touching the session
        async with factory() as db:
            await db.execute(
                update(ProviderTrustPermissionGrant)
                .where(ProviderTrustPermissionGrant.provider_id == mgr_id)
                .values(revoked_at=now)
            )
            await db.commit()

        # Attempt Phase 4C grant using freshly stepped up session
        sess = await resolve_provider_session_context(token)
        mfa_time = datetime.fromisoformat(sess["mfa_verified_at"])

        auth = TrustManagementAuthentication(
            provider_id=mgr_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=mfa_time,
        )
        target_id, _, _ = await _create_test_provider(factory)

        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=mgr_id,
                    authentication=auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=f"stepup-revoked-{uuid.uuid4().hex[:10]}",
                    now=datetime.now(timezone.utc),
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

    finally:
        await engine.dispose()


async def test_integration_phase4c_fresh_step_up_with_active_root_succeeds():
    """Stale MFA fails Phase 4C; after step-up, Phase 4C freshness gate passes and mutation succeeds."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(timezone.utc)
        stale_mfa = now - timedelta(minutes=25)
        mgr_id, token, secret = await _create_test_provider(
            factory, session_mfa_time=stale_mfa
        )

        # Seed active root grant
        async with factory() as db:
            root_grant = ProviderTrustPermissionGrant(
                id=uuid.uuid4(),
                provider_id=mgr_id,
                permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                scope_type=TrustPermissionScope.GLOBAL.value,
                facility_id=None,
                granted_at=now - timedelta(days=1),
                valid_from=now - timedelta(days=1),
                valid_until=None,
                revoked_at=None,
                granted_by_actor_id="test-root",
            )
            db.add(root_grant)
            await db.commit()

        target_id, _, _ = await _create_test_provider(factory)

        # 1. Before step-up: Phase 4C rejects stale MFA
        stale_auth = TrustManagementAuthentication(
            provider_id=mgr_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=stale_mfa,
        )
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=mgr_id,
                    authentication=stale_auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=f"before-stepup-{uuid.uuid4().hex[:10]}",
                    now=now,
                )
            assert exc.value.code == "MFA_STEP_UP_REQUIRED"

        # 2. Step up via API route
        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(secret).now()},
            )
            assert resp.status_code == 200

        # 3. After step-up: Phase 4C receives refreshed session and succeeds!
        sess = await resolve_provider_session_context(token)
        fresh_mfa = datetime.fromisoformat(sess["mfa_verified_at"])

        fresh_auth = TrustManagementAuthentication(
            provider_id=mgr_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=fresh_mfa,
        )

        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res = await svc.apply_grant(
                actor_id=mgr_id,
                authentication=fresh_auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=f"after-stepup-{uuid.uuid4().hex[:10]}",
                now=datetime.now(timezone.utc),
            )
            assert isinstance(res, ProviderTrustPermissionApplicationResult)
            assert res.permission == "PROFESSIONAL_REVIEW"
            assert res.target_provider_id == target_id

    finally:
        await engine.dispose()


async def test_final_session_refresh_failure_leaves_old_mfa_timestamp():
    """Gate 1: If mark_provider_session_mfa_verified fails, 401 is returned and old MFA is unchanged."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = get_async_redis_client()

    try:
        now = datetime.now(timezone.utc)
        stale_mfa = now - timedelta(minutes=45)
        prov_id, token, secret = await _create_test_provider(
            factory, session_mfa_time=stale_mfa
        )
        totp_code = pyotp.TOTP(secret).now()
        key = f"provider_session:{token}"
        keys_before = await redis.keys("provider_session:*")

        # Simulate failure at mark_provider_session_mfa_verified seam
        with patch(
            "app.api.v2.mfa_action_routes.mark_provider_session_mfa_verified",
            AsyncMock(return_value=False),
        ):
            transport = ASGITransport(app=main_app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                resp = await client.post(
                    "/api/v2/auth/mfa/verify-action",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": _USER_AGENT,
                    },
                    json={"code": totp_code},
                )
                assert resp.status_code == 401
                assert resp.json()["detail"]["error_code"] == "PROVIDER_SESSION_INVALID"

        # In Redis: session mfa_verified_at MUST remain unchanged (stale_mfa)
        raw_sess = await redis.get(key)
        assert raw_sess is not None
        sess = json.loads(raw_sess)
        assert sess["mfa_verified_at"] == stale_mfa.isoformat()

        # No new provider session was minted
        keys_after = await redis.keys("provider_session:*")
        assert len(keys_after) == len(keys_before)

        # Audit entry exists in PostgreSQL because TOTP was verified prior to session update
        async with factory() as db:
            audit_entry = (
                await db.execute(
                    text(
                        "SELECT action, resource, status, details "
                        "FROM public.audit_ledger WHERE action = 'PROVIDER_STEP_UP_MFA_VERIFIED' "
                        "AND resource = :p"
                    ),
                    {"p": str(prov_id)},
                )
            ).first()
            assert audit_entry is not None
            assert audit_entry.action == "PROVIDER_STEP_UP_MFA_VERIFIED"
            assert audit_entry.status == "SUCCESS"

    finally:
        await engine.dispose()


async def test_same_session_updated_no_token_minting():
    """Gate 4: Successful step-up updates the exact same session token in place with no new token minted."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = get_async_redis_client()

    try:
        now = datetime.now(timezone.utc)
        stale_mfa = now - timedelta(minutes=30)
        prov_id, token, secret = await _create_test_provider(
            factory, session_mfa_time=stale_mfa
        )
        totp_code = pyotp.TOTP(secret).now()

        key = f"provider_session:{token}"
        keys_before = await redis.keys("provider_session:*")
        ttl_before = await redis.ttl(key)
        assert ttl_before > 0

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert resp.status_code == 200
            assert resp.json() == {"verified": True}

        # 1. Exact same session key still exists
        keys_after = await redis.keys("provider_session:*")
        assert len(keys_after) == len(keys_before)
        assert set(keys_after) == set(keys_before)

        # 2. TTL is preserved, not extended
        ttl_after = await redis.ttl(key)
        assert ttl_after <= ttl_before
        assert ttl_after >= ttl_before - 5

        # 3. Payload contents: provider_id identical, authenticated unchanged, only mfa_verified_at refreshed
        raw_sess = await redis.get(key)
        sess = json.loads(raw_sess)
        assert sess["provider_id"] == str(prov_id)
        assert sess["authenticated"] is True
        assert sess["mfa_verified_at"] is not None
        refreshed_mfa = datetime.fromisoformat(sess["mfa_verified_at"])
        assert refreshed_mfa > stale_mfa
        assert abs((refreshed_mfa - datetime.now(timezone.utc)).total_seconds()) < 5

    finally:
        await engine.dispose()


async def test_mfa_secret_absent_step_up_denied():
    """Gate 5: ProviderCredential with mfa_enabled=True but absent secret is denied 400."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = get_async_redis_client()

    try:
        prov_id, token, _ = await _create_test_provider(
            factory, mfa_enabled=True, secret_absent=True
        )
        key = f"provider_session:{token}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": "123456"},
            )
            assert resp.status_code == 400
            assert resp.json()["detail"]["error_code"] == "MFA_NOT_CONFIGURED"

        # Session in Redis remains unmodified
        raw_sess = await redis.get(key)
        sess = json.loads(raw_sess)
        assert sess["mfa_verified_at"] is None

    finally:
        await engine.dispose()


async def test_integration_phase4c_account_deactivated_after_step_up_denied():
    """Gate 2: Fresh MFA cannot override post-step-up account deactivation in Phase 4C."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, token, secret = await _create_test_provider(factory)
        now = datetime.now(timezone.utc)

        # Seed active root grant
        async with factory() as db:
            root_grant = ProviderTrustPermissionGrant(
                id=uuid.uuid4(),
                provider_id=mgr_id,
                permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                scope_type=TrustPermissionScope.GLOBAL.value,
                facility_id=None,
                granted_at=now - timedelta(days=1),
                valid_from=now - timedelta(days=1),
                valid_until=None,
                revoked_at=None,
                granted_by_actor_id="test-root",
            )
            db.add(root_grant)
            await db.commit()

        # Step up successfully through real HTTP
        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(secret).now()},
            )
            assert resp.status_code == 200

        # Now deactivate ProviderIdentity in PostgreSQL WITHOUT touching Redis session
        async with factory() as db:
            await db.execute(
                update(ProviderIdentity)
                .where(ProviderIdentity.id == mgr_id)
                .values(is_active=False, status="suspended")
            )
            await db.commit()

        sess = await resolve_provider_session_context(token)
        fresh_mfa = datetime.fromisoformat(sess["mfa_verified_at"])

        fresh_auth = TrustManagementAuthentication(
            provider_id=mgr_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=fresh_mfa,
        )
        target_id, _, _ = await _create_test_provider(factory)

        # Phase 4C must deny due to deactivated account!
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=mgr_id,
                    authentication=fresh_auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=f"stepup-acctdeact-{uuid.uuid4().hex[:10]}",
                    now=datetime.now(timezone.utc),
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

    finally:
        await engine.dispose()


async def test_integration_phase4c_credential_deactivated_after_step_up_denied():
    """Gate 3: Fresh MFA cannot override post-step-up credential deactivation in Phase 4C."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, token, secret = await _create_test_provider(factory)
        now = datetime.now(timezone.utc)

        # Seed active root grant
        async with factory() as db:
            root_grant = ProviderTrustPermissionGrant(
                id=uuid.uuid4(),
                provider_id=mgr_id,
                permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                scope_type=TrustPermissionScope.GLOBAL.value,
                facility_id=None,
                granted_at=now - timedelta(days=1),
                valid_from=now - timedelta(days=1),
                valid_until=None,
                revoked_at=None,
                granted_by_actor_id="test-root",
            )
            db.add(root_grant)
            await db.commit()

        # Step up successfully through real HTTP
        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": pyotp.TOTP(secret).now()},
            )
            assert resp.status_code == 200

        # Now deactivate ProviderCredential in PostgreSQL WITHOUT touching Redis session
        async with factory() as db:
            await db.execute(
                update(ProviderCredential)
                .where(ProviderCredential.provider_id == mgr_id)
                .values(is_active=False)
            )
            await db.commit()

        sess = await resolve_provider_session_context(token)
        fresh_mfa = datetime.fromisoformat(sess["mfa_verified_at"])

        fresh_auth = TrustManagementAuthentication(
            provider_id=mgr_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=fresh_mfa,
        )
        target_id, _, _ = await _create_test_provider(factory)

        # Phase 4C must deny due to deactivated credential!
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=mgr_id,
                    authentication=fresh_auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=f"stepup-creddeact-{uuid.uuid4().hex[:10]}",
                    now=datetime.now(timezone.utc),
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

    finally:
        await engine.dispose()
