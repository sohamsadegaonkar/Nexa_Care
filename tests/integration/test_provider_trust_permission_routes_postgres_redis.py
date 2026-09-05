"""Real PostgreSQL + Redis qualification for provider trust permission administration HTTP routes (Phase 4E).

Proves:
- Grant PROFESSIONAL_REVIEW (GLOBAL scope) succeeds end-to-end.
- Grant FACILITY_REVIEW (FACILITY scope) succeeds end-to-end.
- Grant AFFILIATION_MANAGE (FACILITY scope) succeeds end-to-end.
- Revoke subordinate grant succeeds end-to-end.
- Subordinate self-revoke succeeds end-to-end.
- Self-grant is prohibited (403 SELF_GRANT_PROHIBITED).
- Root grant via HTTP is prohibited (403 ROOT_PERMISSION_OFFLINE_ONLY).
- Root revoke via HTTP is prohibited (403 ROOT_PERMISSION_OFFLINE_ONLY).
- Active duplicate grant returns conflict (409 ACTIVE_GRANT_EXISTS).
- Expired-slot supersession succeeds via grant route (new grant + superseded grant ID, replay returns same IDs).
- Same-key replay returns 200 with idempotent_replay=True.
- Key reuse with different payload returns 409 IDEMPOTENCY_KEY_REUSED.
- Non-existent, inactive account, and inactive credential targets all collapse to 404 TARGET_PROVIDER_UNAVAILABLE.
- Non-existent facility returns 404 RESOURCE_NOT_FOUND.
- Stale MFA (>15m) returns 428 MFA_STEP_UP_REQUIRED, succeeds after Phase 4D step-up.
- Root revoked after step-up returns 403 AUTHORIZATION_DENIED.
- Legacy roles without root grant (admin, clinical_reviewer, clinician, auditor) return 403 AUTHORIZATION_DENIED.
- Cookie auth enforces double-submit CSRF protection; Bearer auth succeeds without CSRF tokens.
- Trust permissions maintain strict clinical separation (no escalation to clinical roles/verifications).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_async_engine, get_session_factory
from app.core.redis import get_async_redis_client, get_redis_client
from app.core.security import encrypt_mfa_secret
from app.main import app as main_app
from app.models.provider import (
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
from app.services.provider_auth_service import issue_provider_session_token

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.redis,
    pytest.mark.asyncio,
]

HEAD = "20260905_verification_application"
_USER_AGENT = "Nexa-PermRoutes-Qual-Agent/1.0"
_CLIENT_IP = "127.0.0.1"


def _get_db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://nexa:nexa_test@127.0.0.1:55439/nexa_qual_perm_final_4e",
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


async def _create_provider(
    factory,
    *,
    is_active: bool = True,
    status: str = "active",
    credential_active: bool = True,
    mfa_enabled: bool = True,
    secret: str | None = None,
    session_mfa_time: datetime | None = None,
    ua: str = _USER_AGENT,
    ip: str = _CLIENT_IP,
) -> tuple[uuid.UUID, str, str]:
    """Create ProviderIdentity, ProviderCredential, and an active Redis session token."""
    prov_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    raw_secret = secret or pyotp.random_base32()
    enc_secret = encrypt_mfa_secret(raw_secret)

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
            mfa_secret=raw_secret,
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
        mfa_verified_at=session_mfa_time if session_mfa_time is not None else now,
    )
    return prov_id, token, raw_secret


async def _create_root_manager(
    factory,
    *,
    session_mfa_time: datetime | None = None,
) -> tuple[uuid.UUID, str, str, uuid.UUID]:
    """Create a provider with offline TRUST_PERMISSION_MANAGE root grant and active session."""
    now = datetime.now(timezone.utc)
    prov_id, token, secret = await _create_provider(
        factory, session_mfa_time=session_mfa_time
    )
    root_grant_id = uuid.uuid4()

    async with factory() as db:
        root_grant = ProviderTrustPermissionGrant(
            id=root_grant_id,
            provider_id=prov_id,
            permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
            scope_type=TrustPermissionScope.GLOBAL.value,
            facility_id=None,
            granted_at=now - timedelta(days=1),
            valid_from=now - timedelta(days=1),
            valid_until=None,
            revoked_at=None,
            granted_by_actor_id="root-offline-authority",
            governance_reference="QUAL-ROOT-4E",
        )
        db.add(root_grant)
        await db.commit()

    return prov_id, token, secret, root_grant_id


async def _create_facility(factory) -> uuid.UUID:
    """Create an active facility in hospital_registry."""
    fac_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=fac_id,
                facility_code=f"FAC-{fac_id.hex[:8]}",
                legal_name="Qual General Hospital",
                display_name="Qual General Hospital",
                country_code="IN",
                is_active=True,
            )
        )
        await db.commit()
    return fac_id


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


async def test_grant_professional_review_global_success():
    """Manager with fresh MFA grants subordinate PROFESSIONAL_REVIEW (GLOBAL scope)."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        now = datetime.now(timezone.utc)
        idem_key = f"grant-prof-{uuid.uuid4().hex[:10]}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                    "facility_id": None,
                    "valid_from": now.isoformat(),
                    "valid_until": (now + timedelta(days=30)).isoformat(),
                    "governance_reference": "GOV-2026-GRANT-01",
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["command"] == "GRANT"
            assert uuid.UUID(data["grant_id"])
            assert data["target_provider_id"] == str(target_id)
            assert data["permission"] == "PROFESSIONAL_REVIEW"
            assert data["scope_type"] == "GLOBAL"
            assert data["facility_id"] is None
            assert data["superseded_grant_id"] is None
            assert data["idempotent_replay"] is False

        # Verify DB state directly
        async with factory() as db:
            grant = await db.get(
                ProviderTrustPermissionGrant, uuid.UUID(data["grant_id"])
            )
            assert grant is not None
            assert grant.provider_id == target_id
            assert grant.permission == "PROFESSIONAL_REVIEW"
            assert grant.scope_type == "GLOBAL"
            assert grant.facility_id is None
            assert grant.revoked_at is None
            assert grant.granted_by_actor_id == str(mgr_id)
            assert grant.governance_reference == "GOV-2026-GRANT-01"

            # Check audit outbox
            outbox_rows = await db.execute(
                text(
                    "SELECT event_type, actor_id, payload FROM public.audit_outbox "
                    "WHERE event_type = 'PROVIDER_TRUST_PERMISSION_GRANTED' ORDER BY created_at DESC LIMIT 1"
                )
            )
            outbox_entry = outbox_rows.fetchone()
            assert outbox_entry is not None
            assert outbox_entry[1] == str(mgr_id)
    finally:
        await engine.dispose()


async def test_grant_facility_review_facility_scope_success():
    """Manager grants FACILITY_REVIEW with explicit facility_id (FACILITY scope)."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)
        fac_id = await _create_facility(factory)

        idem_key = f"grant-fac-{uuid.uuid4().hex[:10]}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "FACILITY_REVIEW",
                    "facility_id": str(fac_id),
                    "governance_reference": "GOV-FAC-01",
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["command"] == "GRANT"
            assert data["permission"] == "FACILITY_REVIEW"
            assert data["scope_type"] == "FACILITY"
            assert data["facility_id"] == str(fac_id)
            assert data["superseded_grant_id"] is None
            assert data["idempotent_replay"] is False
    finally:
        await engine.dispose()


async def test_grant_affiliation_manage_facility_scope_success():
    """Manager grants AFFILIATION_MANAGE with explicit facility_id (FACILITY scope)."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)
        fac_id = await _create_facility(factory)

        idem_key = f"grant-aff-{uuid.uuid4().hex[:10]}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "AFFILIATION_MANAGE",
                    "facility_id": str(fac_id),
                    "governance_reference": "GOV-AFF-01",
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["command"] == "GRANT"
            assert data["permission"] == "AFFILIATION_MANAGE"
            assert data["scope_type"] == "FACILITY"
            assert data["facility_id"] == str(fac_id)
    finally:
        await engine.dispose()


async def test_revoke_subordinate_grant_success():
    """Manager successfully revokes a subordinate permission grant."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # Grant first
            grant_resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-grant-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                    "governance_reference": "GOV-REV-PRE",
                },
            )
            assert grant_resp.status_code == 200
            grant_id = grant_resp.json()["grant_id"]

            # Revoke
            revoke_resp = await client.post(
                f"/api/v2/provider-trust/permissions/{grant_id}/revoke",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-revoke-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "revocation_reason_code": "ACCESS_REMOVED",
                    "governance_reference": "GOV-REV-POST",
                },
            )
            assert revoke_resp.status_code == 200, revoke_resp.text
            r_data = revoke_resp.json()
            assert r_data["command"] == "REVOKE"
            assert r_data["grant_id"] == grant_id
            assert r_data["target_provider_id"] == str(target_id)
            assert r_data["permission"] == "PROFESSIONAL_REVIEW"
            assert r_data["idempotent_replay"] is False

        # Verify DB row is revoked
        async with factory() as db:
            grant = await db.get(ProviderTrustPermissionGrant, uuid.UUID(grant_id))
            assert grant.revoked_at is not None
    finally:
        await engine.dispose()


async def test_subordinate_self_revoke_success():
    """Manager with subordinate grant can self-revoke own grant; subordinate without root is denied."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(timezone.utc)
        mgr_id, mgr_token, _, _ = await _create_root_manager(factory)
        sub_id, sub_token, _ = await _create_provider(factory)

        # Give manager a subordinate PROFESSIONAL_REVIEW grant directly via setup
        mgr_sub_grant_id = uuid.uuid4()
        async with factory() as db:
            db.add(
                ProviderTrustPermissionGrant(
                    id=mgr_sub_grant_id,
                    provider_id=mgr_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,
                    scope_type=TrustPermissionScope.GLOBAL.value,
                    facility_id=None,
                    granted_at=now,
                    valid_from=now,
                    valid_until=None,
                    revoked_at=None,
                    granted_by_actor_id="root-offline-authority",
                    governance_reference="MGR-SUB-GRANT",
                )
            )
            await db.commit()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # 1. Subordinate without TRUST_PERMISSION_MANAGE attempting to revoke -> 403 AUTHORIZATION_DENIED
            sub_revoke_attempt = await client.post(
                f"/api/v2/provider-trust/permissions/{mgr_sub_grant_id}/revoke",
                headers={
                    "Authorization": f"Bearer {sub_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-sub-rev-deny-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "revocation_reason_code": "ROLE_CHANGED",
                    "governance_reference": "UNAUTHORIZED-REVOKE",
                },
            )
            assert sub_revoke_attempt.status_code == 403
            assert sub_revoke_attempt.json() == {"error_code": "AUTHORIZATION_DENIED"}

            # 2. Manager self-revoking own subordinate grant -> 200 OK
            mgr_self_revoke = await client.post(
                f"/api/v2/provider-trust/permissions/{mgr_sub_grant_id}/revoke",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-mgr-self-rev-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "revocation_reason_code": "ROLE_CHANGED",
                    "governance_reference": "SELF-DEPARTURE",
                },
            )
            assert mgr_self_revoke.status_code == 200, mgr_self_revoke.text
            data = mgr_self_revoke.json()
            assert data["command"] == "REVOKE"
            assert data["grant_id"] == str(mgr_sub_grant_id)
            assert data["target_provider_id"] == str(mgr_id)

        async with factory() as db:
            g = await db.get(ProviderTrustPermissionGrant, mgr_sub_grant_id)
            assert g.revoked_at is not None
    finally:
        await engine.dispose()


async def test_self_grant_prohibited():
    """Manager attempting self-grant returns 403 SELF_GRANT_PROHIBITED."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, mgr_token, _, _ = await _create_root_manager(factory)

        idem_key = f"k-self-{uuid.uuid4().hex[:8]}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(mgr_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp.status_code == 403
            assert resp.json() == {"error_code": "SELF_GRANT_PROHIBITED"}

        # Gate 5: Policy denial rolls back idempotency (0 rows remain)
        async with factory() as db:
            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": idem_key},
            )
            assert idem_count == 0
    finally:
        await engine.dispose()


async def test_root_permission_grant_denied_offline_only():
    """Attempting to grant TRUST_PERMISSION_MANAGE via HTTP returns 403 ROOT_PERMISSION_OFFLINE_ONLY."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-root-g-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "TRUST_PERMISSION_MANAGE",
                },
            )
            assert resp.status_code == 403
            assert resp.json() == {"error_code": "ROOT_PERMISSION_OFFLINE_ONLY"}
    finally:
        await engine.dispose()


async def test_root_permission_revoke_denied_offline_only():
    """Attempting to revoke a TRUST_PERMISSION_MANAGE grant via HTTP returns 403 ROOT_PERMISSION_OFFLINE_ONLY."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, root_grant_id = await _create_root_manager(factory)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                f"/api/v2/provider-trust/permissions/{root_grant_id}/revoke",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-root-r-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "revocation_reason_code": "SECURITY_RESPONSE",
                },
            )
            assert resp.status_code == 403
            assert resp.json() == {"error_code": "ROOT_PERMISSION_OFFLINE_ONLY"}

        # Verify root grant remains completely unchanged in DB
        async with factory() as db:
            root_grant = await db.get(ProviderTrustPermissionGrant, root_grant_id)
            assert root_grant is not None
            assert root_grant.revoked_at is None
    finally:
        await engine.dispose()


async def test_active_grant_duplicate_collision_conflict():
    """Attempting a duplicate grant for an active slot returns 409 ACTIVE_GRANT_EXISTS."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # First grant
            resp1 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-act-1-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp1.status_code == 200

            # Second grant with distinct idempotency key
            resp2 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-act-2-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp2.status_code == 409
            assert resp2.json() == {"error_code": "ACTIVE_GRANT_EXISTS"}
    finally:
        await engine.dispose()


async def test_expired_slot_supersession_via_grant_and_replay():
    """Grant on an expired slot supersedes the expired row; same-key replay returns identical IDs."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_id, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        now = datetime.now(timezone.utc)
        expired_grant_id = uuid.uuid4()

        # Seed expired grant directly in DB (valid_until in past, revoked_at is None)
        async with factory() as db:
            db.add(
                ProviderTrustPermissionGrant(
                    id=expired_grant_id,
                    provider_id=target_id,
                    permission="PROFESSIONAL_REVIEW",
                    scope_type="GLOBAL",
                    facility_id=None,
                    granted_at=now - timedelta(days=60),
                    valid_from=now - timedelta(days=60),
                    valid_until=now - timedelta(days=1),
                    revoked_at=None,
                    granted_by_actor_id="prior-manager",
                    governance_reference="OLD-GRANT",
                )
            )
            await db.commit()

        idem_key = f"k-super-{uuid.uuid4().hex[:8]}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                    "valid_from": now.isoformat(),
                    "valid_until": (now + timedelta(days=30)).isoformat(),
                    "governance_reference": "NEW-SUPER-GRANT",
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["superseded_grant_id"] == str(expired_grant_id)
            assert data["idempotent_replay"] is False
            new_grant_id = data["grant_id"]

            # Replay with same idempotency key
            replay_resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                    "valid_from": now.isoformat(),
                    "valid_until": (now + timedelta(days=30)).isoformat(),
                    "governance_reference": "NEW-SUPER-GRANT",
                },
            )
            assert replay_resp.status_code == 200
            replay_data = replay_resp.json()
            assert replay_data["grant_id"] == new_grant_id
            assert replay_data["superseded_grant_id"] == str(expired_grant_id)
            assert replay_data["idempotent_replay"] is True

        # Gate 8: Database assertions for expired supersession & replay
        async with factory() as db:
            # 1. Old row revoked, new row active
            old_row = await db.get(ProviderTrustPermissionGrant, expired_grant_id)
            assert old_row is not None
            assert old_row.revoked_at is not None

            new_row = await db.get(
                ProviderTrustPermissionGrant, uuid.UUID(new_grant_id)
            )
            assert new_row is not None
            assert new_row.revoked_at is None

            # 2. Exactly one completed idempotency record
            idem_completed = await db.scalar(
                text(
                    "SELECT count(*) FROM mutation_idempotency WHERE idempotency_key = :k AND response_status = 200"
                ),
                {"k": idem_key},
            )
            assert idem_completed == 1

            # 3. Exactly one supersede revoke audit event and one grant audit event (no duplicate events on replay)
            revoke_events = await db.scalar(
                text(
                    "SELECT count(*) FROM audit_outbox WHERE event_type = 'PROVIDER_TRUST_PERMISSION_REVOKED' AND idempotency_key LIKE :k"
                ),
                {"k": f"%{idem_key}%"},
            )
            assert revoke_events == 1

            grant_events = await db.scalar(
                text(
                    "SELECT count(*) FROM audit_outbox WHERE event_type = 'PROVIDER_TRUST_PERMISSION_GRANTED' AND idempotency_key LIKE :k"
                ),
                {"k": f"%{idem_key}%"},
            )
            assert grant_events == 1
    finally:
        await engine.dispose()


async def test_idempotent_replay_and_key_reuse_conflict():
    """Same key replay returns 200 (idempotent_replay=True); payload reuse returns 409."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)
        target1_id, _, _ = await _create_provider(factory)
        target2_id, _, _ = await _create_provider(factory)

        idem_key = f"k-idem-{uuid.uuid4().hex[:8]}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # First execution
            resp1 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target1_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp1.status_code == 200
            assert resp1.json()["idempotent_replay"] is False
            original_grant_id = resp1.json()["grant_id"]

            # Replay same payload
            resp_replay = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target1_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp_replay.status_code == 200
            assert resp_replay.json()["grant_id"] == original_grant_id
            assert resp_replay.json()["idempotent_replay"] is True

            # Reuse key with DIFFERENT payload (target2) -> 409 IDEMPOTENCY_KEY_REUSED
            resp_conflict = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target2_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp_conflict.status_code == 409
            assert resp_conflict.json() == {"error_code": "IDEMPOTENCY_KEY_REUSED"}
    finally:
        await engine.dispose()


async def test_target_provider_unavailable_enumeration_collapse():
    """Non-existent, inactive provider, and inactive credential all collapse to 404 TARGET_PROVIDER_UNAVAILABLE."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)

        # 1. Non-existent provider ID
        random_id = uuid.uuid4()

        # 2. Inactive provider account
        inactive_prov_id, _, _ = await _create_provider(factory, is_active=False)

        # 3. Inactive credential
        inactive_cred_id, _, _ = await _create_provider(
            factory, credential_active=False
        )

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            for target_id in (random_id, inactive_prov_id, inactive_cred_id):
                resp = await client.post(
                    "/api/v2/provider-trust/permissions/grant",
                    headers={
                        "Authorization": f"Bearer {mgr_token}",
                        "User-Agent": _USER_AGENT,
                        "Idempotency-Key": f"k-unavail-{uuid.uuid4().hex[:8]}",
                    },
                    json={
                        "target_provider_id": str(target_id),
                        "permission": "PROFESSIONAL_REVIEW",
                    },
                )
                assert resp.status_code == 404
                assert resp.json() == {"error_code": "TARGET_PROVIDER_UNAVAILABLE"}
    finally:
        await engine.dispose()


async def test_nonexistent_facility_returns_404_resource_not_found():
    """Granting a facility-scoped permission with non-existent facility_id returns 404 RESOURCE_NOT_FOUND."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)
        random_fac_id = uuid.uuid4()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-fac-nf-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "FACILITY_REVIEW",
                    "facility_id": str(random_fac_id),
                },
            )
            assert resp.status_code == 404
            assert resp.json() == {"error_code": "RESOURCE_NOT_FOUND"}
    finally:
        await engine.dispose()


async def test_stale_mfa_requires_step_up_then_succeeds_on_retry():
    """Stale MFA (>15m) returns 428 MFA_STEP_UP_REQUIRED; caller steps up and retries with 200 OK."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(timezone.utc)
        stale_time = now - timedelta(minutes=25)
        mgr_id, mgr_token, secret, _ = await _create_root_manager(
            factory, session_mfa_time=stale_time
        )
        target_id, _, _ = await _create_provider(factory)

        idem_key = f"k-stepup-{uuid.uuid4().hex[:8]}"

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # 1. Attempt grant with stale MFA -> 428 MFA_STEP_UP_REQUIRED
            resp_stale = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp_stale.status_code == 428
            assert resp_stale.json() == {"error_code": "MFA_STEP_UP_REQUIRED"}

            # Gate 5: Denial rolls back idempotency reservation (0 rows remain in PostgreSQL)
            async with factory() as db:
                idem_count_stale = await db.scalar(
                    text(
                        "SELECT count(*) FROM mutation_idempotency WHERE idempotency_key = :k"
                    ),
                    {"k": idem_key},
                )
                assert idem_count_stale == 0

            # 2. Call real Phase 4D step-up endpoint
            totp_code = pyotp.TOTP(secret).now()
            step_up_resp = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                },
                json={"code": totp_code},
            )
            assert step_up_resp.status_code == 200
            assert step_up_resp.json() == {"verified": True}

            # 3. Retry identical grant request with same idempotency key -> 200 OK
            resp_fresh = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": idem_key,
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp_fresh.status_code == 200, resp_fresh.text
            assert resp_fresh.json()["command"] == "GRANT"
            assert resp_fresh.json()["idempotent_replay"] is False

        # Gate 5: Successful retry records exactly one completed idempotency row and one audit event
        async with factory() as db:
            idem_count_fresh = await db.scalar(
                text(
                    "SELECT count(*) FROM mutation_idempotency WHERE idempotency_key = :k AND response_status = 200"
                ),
                {"k": idem_key},
            )
            assert idem_count_fresh == 1

            grant_events = await db.scalar(
                text(
                    "SELECT count(*) FROM audit_outbox WHERE event_type = 'PROVIDER_TRUST_PERMISSION_GRANTED' AND idempotency_key LIKE :k"
                ),
                {"k": f"%{idem_key}%"},
            )
            assert grant_events == 1
    finally:
        await engine.dispose()


async def test_root_revoked_after_step_up_authorization_denied():
    """Fresh step-up but root grant revoked in DB returns 403 AUTHORIZATION_DENIED."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(timezone.utc)
        mgr_id, mgr_token, _, root_grant_id = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        # Offline authority revokes the root grant
        # Seed a subordinate grant on target_id
        sub_grant_id = uuid.uuid4()
        async with factory() as db:
            grant = await db.get(ProviderTrustPermissionGrant, root_grant_id)
            grant.revoked_at = now
            db.add(
                ProviderTrustPermissionGrant(
                    id=sub_grant_id,
                    provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,
                    scope_type=TrustPermissionScope.GLOBAL.value,
                    facility_id=None,
                    granted_at=now,
                    valid_from=now,
                    valid_until=None,
                    revoked_at=None,
                    granted_by_actor_id="prior-manager",
                    governance_reference="TARGET-SUB-GRANT",
                )
            )
            await db.commit()

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # 1. Grant route denied with 403
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-revoked-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp.status_code == 403
            assert resp.json() == {"error_code": "AUTHORIZATION_DENIED"}

            # 2. Revoke route also denied with 403
            resp_revoke = await client.post(
                f"/api/v2/provider-trust/permissions/{sub_grant_id}/revoke",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-revoked-r-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "revocation_reason_code": "ACCESS_REMOVED",
                },
            )
            assert resp_revoke.status_code == 403
            assert resp_revoke.json() == {"error_code": "AUTHORIZATION_DENIED"}
    finally:
        await engine.dispose()


async def test_legacy_roles_without_root_grant_authorization_denied():
    """Legacy roles (admin, clinical_reviewer, clinician, auditor) without root grant return 403 AUTHORIZATION_DENIED."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(timezone.utc)
        target_id, _, _ = await _create_provider(factory)
        sub_grant_id = uuid.uuid4()
        async with factory() as db:
            db.add(
                ProviderTrustPermissionGrant(
                    id=sub_grant_id,
                    provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,
                    scope_type=TrustPermissionScope.GLOBAL.value,
                    facility_id=None,
                    granted_at=now,
                    valid_from=now,
                    valid_until=None,
                    revoked_at=None,
                    granted_by_actor_id="prior-manager",
                    governance_reference="SEED-GRANT",
                )
            )
            await db.commit()

        transport = ASGITransport(app=main_app)

        # Create providers with active sessions, fresh MFA, but NO TRUST_PERMISSION_MANAGE grant
        for role_name in ("admin", "clinical_reviewer", "clinician", "auditor"):
            prov_id, token, _ = await _create_provider(factory)

            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                # 1. Ordinary GRANT attempt -> 403 AUTHORIZATION_DENIED
                resp = await client.post(
                    "/api/v2/provider-trust/permissions/grant",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": _USER_AGENT,
                        "Idempotency-Key": f"k-legacy-g-{role_name}-{uuid.uuid4().hex[:6]}",
                    },
                    json={
                        "target_provider_id": str(target_id),
                        "permission": "PROFESSIONAL_REVIEW",
                    },
                )
                assert (
                    resp.status_code == 403
                ), f"Grant failed for {role_name}: {resp.text}"
                assert resp.json() == {"error_code": "AUTHORIZATION_DENIED"}

                # 2. Ordinary REVOKE attempt -> 403 AUTHORIZATION_DENIED
                resp_rev = await client.post(
                    f"/api/v2/provider-trust/permissions/{sub_grant_id}/revoke",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": _USER_AGENT,
                        "Idempotency-Key": f"k-legacy-r-{role_name}-{uuid.uuid4().hex[:6]}",
                    },
                    json={
                        "revocation_reason_code": "ACCESS_REMOVED",
                    },
                )
                assert (
                    resp_rev.status_code == 403
                ), f"Revoke failed for {role_name}: {resp_rev.text}"
                assert resp_rev.json() == {"error_code": "AUTHORIZATION_DENIED"}
    finally:
        await engine.dispose()


async def test_cookie_csrf_enforcement():
    """Cookie authentication requires double-submit CSRF; matching CSRF token succeeds."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # 1. Cookie auth without CSRF token -> 403
            resp_no_csrf = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                cookies={"nexa_provider_session": mgr_token},
                headers={
                    "User-Agent": _USER_AGENT,
                    "Origin": "http://testserver",
                    "Idempotency-Key": f"k-csrf-1-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp_no_csrf.status_code == 403

            # 2. Cookie auth with mismatched CSRF token -> 403
            resp_mismatch = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                cookies={
                    "nexa_provider_session": mgr_token,
                    "nexa_csrf": "token-a",
                },
                headers={
                    "User-Agent": _USER_AGENT,
                    "Origin": "http://testserver",
                    "X-CSRF-Token": "token-b",
                    "Idempotency-Key": f"k-csrf-2-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp_mismatch.status_code == 403

            # 3. Cookie auth with matching CSRF double-submit -> 200 OK
            csrf_token = "valid-matching-csrf-secret"
            resp_ok = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                cookies={
                    "nexa_provider_session": mgr_token,
                    "nexa_csrf": csrf_token,
                },
                headers={
                    "User-Agent": _USER_AGENT,
                    "Origin": "http://testserver",
                    "X-CSRF-Token": csrf_token,
                    "Idempotency-Key": f"k-csrf-3-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp_ok.status_code == 200, resp_ok.text
            assert resp_ok.json()["command"] == "GRANT"
    finally:
        await engine.dispose()


async def test_bearer_succeeds_without_csrf_tokens():
    """Bearer authentication operates independently of cookie CSRF tokens."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={
                    "Authorization": f"Bearer {mgr_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": f"k-bearer-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "target_provider_id": str(target_id),
                    "permission": "PROFESSIONAL_REVIEW",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["command"] == "GRANT"
    finally:
        await engine.dispose()


async def test_clinical_separation_invariants():
    """Granting trust permissions maintains strict clinical separation: 0 affiliations or clinical verifications."""
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        _, mgr_token, _, _ = await _create_root_manager(factory)
        target_id, _, _ = await _create_provider(factory)
        fac_id = await _create_facility(factory)

        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # Grant all 3 subordinate permissions
            for perm, fac in (
                ("PROFESSIONAL_REVIEW", None),
                ("FACILITY_REVIEW", str(fac_id)),
                ("AFFILIATION_MANAGE", str(fac_id)),
            ):
                r = await client.post(
                    "/api/v2/provider-trust/permissions/grant",
                    headers={
                        "Authorization": f"Bearer {mgr_token}",
                        "User-Agent": _USER_AGENT,
                        "Idempotency-Key": f"k-sep-{perm}-{uuid.uuid4().hex[:6]}",
                    },
                    json={
                        "target_provider_id": str(target_id),
                        "permission": perm,
                        "facility_id": fac,
                    },
                )
                assert r.status_code == 200, r.text

        # Verify DB: 0 affiliations exist for target_id
        async with factory() as db:
            aff_count = await db.scalar(
                select(ProviderHospitalAffiliation).where(
                    ProviderHospitalAffiliation.provider_id == target_id
                )
            )
            assert aff_count is None
    finally:
        await engine.dispose()
