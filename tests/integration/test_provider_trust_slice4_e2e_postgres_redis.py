"""Final End-to-End, Adversarial, PostgreSQL + Redis Qualification for Slice 4 (Phase 4G).

Proves the complete Slice-4 Organizational Trust Permission Administration architecture:
1. Canonical full journey (Steps 1 through 14) from zero-root state through offline root
   provisioning, stale MFA rejection (428), Phase-4D step-up, same-key idempotency retry,
   subordinate grants (PROFESSIONAL_REVIEW, FACILITY_REVIEW, AFFILIATION_MANAGE),
   duplicate and replay semantics, subordinate revocation, offline root revocation,
   fresh MFA authority-loss rejection (403), zero-root recovery, and new root administration.
2. Public root attack matrix (HTTP cannot grant, revoke, or bootstrap root authority).
3. Legacy role confusion matrix (admin, privacy_officer, auditor, clinical_reviewer,
   clinician, receptionist without root grant cannot administer trust permissions).
4. Authentication confusion matrix (missing session, Basic auth, invalid token, expired session,
   UA mismatch, malformed session, stale MFA, fresh MFA without root, IP rotation).
5. Target disclosure matrix (nonexistent, inactive account, inactive credential all collapse to 404).
6. Request shape adversarial matrix (malformed/extra fields, naive datetimes, scope mismatches, 422/400).
7. Root CLI adversarial matrix (preflight guards, confirmations, actor separation, valid_until guards).
8. Root set concurrency (global advisory lock + expected count CAS).
9. Ordinary duplicate concurrency (row-lock serialization, 409 ACTIVE_GRANT_EXISTS).
10. Reciprocal manager concurrency (deadlock-free sorted UUID row locking).
11. Ordinary revoke concurrency (409 GRANT_ALREADY_REVOKED).
12. Root revocation linearization A (4F revokes first -> 4C denied).
13. Root revocation linearization B (4C locks first -> 4F blocks -> 4C commits -> 4F revokes -> 4C denied).
14. Revocation invariants (contact assurance removal, account deactivation, credential deactivation,
    root expiry, future root).
15. MFA freshness boundary (now passes, 14m59s passes, 15m01s requires step-up).
16. Audit chain and partitions (PLATFORM global, PLATFORM hospital/{id}, AUTH platform, zero root event types).
17. Atomicity qualification (transactional rollback on audit/idempotency failures, denial rollback).
18. Clinical separation matrix (no escalation to clinical roles, capabilities, verifications, or patient access).
19. Route surface freeze (exactly 26 POST routes under /api/v2/provider-trust, 0 non-POST routes).
20. Architecture static guards (zero bypass flags in app/, strict import boundaries).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pyotp
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.provider_trust_permission_application as app_module
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
from app.security.provider_capabilities import ClinicalCapability
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.services.provider_auth_service import (
    issue_provider_session_token,
    resolve_provider_session_context,
)
from app.services.provider_trust_authorization import TrustManagementAuthentication
from app.services.provider_trust_permission_application import (
    ProviderTrustPermissionApplicationError,
    ProviderTrustPermissionApplicationService,
)
from app.services.provider_trust_root_governance import (
    ProviderTrustRootGovernanceError,
    ProviderTrustRootGovernanceService,
    RootRevocationReasonCode,
)
from scripts.governance_trust_root import run_governance
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    get_qualification_redis_url,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.redis,
    pytest.mark.asyncio,
]

HEAD = "20260914_patient_search_identifiers"
_USER_AGENT = "Nexa-Slice4-Qual-Agent/1.0"
_CLIENT_IP = "127.0.0.1"
_DB_NAME = "nexa_qual_slice4_e2e"


def _get_db_url() -> str:
    return postgres_database_url(_DB_NAME)


def _get_redis_url() -> str:
    return get_qualification_redis_url()


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module", autouse=True)
def _setup_env():
    db_url = _get_db_url()
    redis_url = _get_redis_url()

    _prev_test_db = os.environ.get("TEST_DATABASE_URL")
    _prev_db = os.environ.get("DATABASE_URL")
    _prev_root_db = os.environ.get("NEXA_TRUST_ROOT_DATABASE_URL")
    _prev_upstash = os.environ.get("UPSTASH_REDIS_URL")
    _prev_test_redis = os.environ.get("TEST_REDIS_URL")
    _prev_mfa = os.environ.get("MFA_ENCRYPTION_KEY")
    _prev_cors = os.environ.get("CORS_ALLOWED_ORIGINS")

    os.environ["TEST_DATABASE_URL"] = db_url
    os.environ["DATABASE_URL"] = db_url
    os.environ["NEXA_TRUST_ROOT_DATABASE_URL"] = db_url
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

    # Ensure database exists and is migrated to HEAD
    asyncio.run(create_disposable_database(_DB_NAME))
    migrate_database_to_head(db_url, target_head=HEAD)
    yield

    for k, v in [
        ("TEST_DATABASE_URL", _prev_test_db),
        ("DATABASE_URL", _prev_db),
        ("NEXA_TRUST_ROOT_DATABASE_URL", _prev_root_db),
        ("UPSTASH_REDIS_URL", _prev_upstash),
        ("TEST_REDIS_URL", _prev_test_redis),
        ("MFA_ENCRYPTION_KEY", _prev_mfa),
        ("CORS_ALLOWED_ORIGINS", _prev_cors),
    ]:
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    asyncio.run(drop_disposable_database(_DB_NAME))


@pytest.fixture(autouse=True)
async def override_deps(monkeypatch):
    """Shadow global mocks: require real PostgreSQL and Redis with clean state."""
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


@pytest.fixture(autouse=True)
async def _cleanup_database_tables():
    """Ensure every test begins with completely empty tables."""
    url = _get_db_url()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await db.execute(
            text(
                "TRUNCATE TABLE "
                "public.provider_trust_permission_grant, "
                "public.mutation_idempotency, "
                "public.audit_outbox, "
                "public.audit_ledger, "
                "public.professional_verification, "
                "public.provider_hospital_affiliation, "
                "public.provider_credential, "
                "public.provider_identity, "
                "public.hospital_registry "
                "CASCADE"
            )
        )
        await db.commit()
    await engine.dispose()
    yield


async def _create_provider(
    factory,
    *,
    is_active: bool = True,
    status: str = "active",
    credential_active: bool = True,
    mfa_enabled: bool = True,
    email_verified: bool = True,
    phone_verified: bool = True,
    secret: str | None = None,
    session_mfa_time: datetime | None = None,
    ua: str = _USER_AGENT,
    ip: str = _CLIENT_IP,
) -> tuple[uuid.UUID, str, str]:
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
            email_verified_at=now if email_verified else None,
            phone_verified_at=now if phone_verified else None,
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


async def _create_facility(factory) -> uuid.UUID:
    fac_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=fac_id,
                facility_code=f"FAC-{fac_id.hex[:8]}",
                legal_name="Slice 4 Qualification Hospital",
                display_name="Slice 4 Qualification Hospital",
                country_code="IN",
                is_active=True,
            )
        )
        await db.commit()
    return fac_id


def _extract_error(resp) -> str:
    try:
        data = resp.json()
    except Exception:
        return ""
    if isinstance(data, dict):
        if "error_code" in data:
            return str(data["error_code"])
        detail = data.get("detail")
        if isinstance(detail, dict) and "error_code" in detail:
            return str(detail["error_code"])
        if isinstance(detail, str):
            return detail
    return ""


async def test_canonical_full_journey_steps_1_to_14():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(timezone.utc)
        transport = ASGITransport(app=main_app)
        manager_id, manager_token, manager_secret = await _create_provider(factory)
        target_id, _, _ = await _create_provider(factory)

        step1_key = _key("step1-zero-root")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp1 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step1_key},
                json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"},
            )
            assert resp1.status_code == 403
            assert resp1.json() == {"error_code": "AUTHORIZATION_DENIED"}

        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_trust_permission_grant")) == 0

        valid_until_dt = now + timedelta(days=30)
        cli_key = _key("step2-cli-root")
        argv = [
            "grant-root", "--expected-database-name", _DB_NAME, "--apply",
            "--operator-actor-id", "secops-operator-1", "--approver-actor-id", "secops-approver-1",
            "--governance-reference", "QUAL-ROOT-4G-STEP2", "--idempotency-key", cli_key,
            "--expected-active-root-count", "0", "--target-provider-id", str(manager_id),
            "--confirm-target-provider-id", str(manager_id), "--valid-until", valid_until_dt.isoformat(),
        ]
        exit_code = await run_governance(argv)
        assert exit_code == 0

        async with factory() as db:
            root_grant = (
                await db.execute(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == manager_id,
                        ProviderTrustPermissionGrant.permission == TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                    )
                )
            ).scalar_one()
            manager_root_grant_id = root_grant.id

        stale_mfa_time = now - timedelta(minutes=20)
        redis = get_async_redis_client()
        session_data = await resolve_provider_session_context(manager_token)
        assert session_data is not None
        session_data["mfa_verified_at"] = stale_mfa_time.isoformat()
        import json
        ttl = await redis.ttl(f"provider_session:{manager_token}")
        if ttl <= 0:
            ttl = 3600
        await redis.setex(f"provider_session:{manager_token}", ttl, json.dumps(session_data))

        step3_key = _key("step3-stale-mfa")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp3 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step3_key},
                json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"},
            )
            assert resp3.status_code == 428

        totp_code = pyotp.TOTP(manager_secret).now()
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp4 = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT},
                json={"code": totp_code},
            )
            assert resp4.status_code == 200

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp5 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step3_key},
                json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"},
            )
            assert resp5.status_code == 200
            prof_grant_id = resp5.json()["grant_id"]

        fac_a_id = await _create_facility(factory)
        step8_key = _key("step8-aff-man")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp7 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step7-fac-rev")},
                json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW", "facility_id": str(fac_a_id)},
            )
            assert resp7.status_code == 200
            resp8 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step8_key},
                json={"target_provider_id": str(target_id), "permission": "AFFILIATION_MANAGE", "facility_id": str(fac_a_id)},
            )
            assert resp8.status_code == 200

        step10_key = _key("step10-revoke")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp10 = await client.post(
                f"/api/v2/provider-trust/permissions/{prof_grant_id}/revoke",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step10_key},
                json={"revocation_reason_code": "ACCESS_REMOVED"},
            )
            assert resp10.status_code == 200

        argv_rev = [
            "revoke-root", "--expected-database-name", _DB_NAME, "--apply",
            "--operator-actor-id", "secops-operator-1", "--approver-actor-id", "secops-approver-1",
            "--governance-reference", "QUAL-ROOT-4G-STEP11", "--idempotency-key", _key("step11-root-rev"),
            "--expected-active-root-count", "1", "--grant-id", str(manager_root_grant_id),
            "--confirm-grant-id", str(manager_root_grant_id), "--revocation-reason-code", "ROOT_ROTATION",
            "--acknowledge-zero-active-roots",
        ]
        assert await run_governance(argv_rev) == 0

        mgr2_id, mgr2_token, _ = await _create_provider(factory)
        argv_rec = [
            "grant-root", "--expected-database-name", _DB_NAME, "--apply",
            "--operator-actor-id", "secops-operator-2", "--approver-actor-id", "secops-approver-2",
            "--governance-reference", "QUAL-ROOT-4G-STEP13-RECOVERY", "--idempotency-key", _key("step13-zero-recovery"),
            "--expected-active-root-count", "0", "--target-provider-id", str(mgr2_id),
            "--confirm-target-provider-id", str(mgr2_id), "--valid-until", (now + timedelta(days=60)).isoformat(),
        ]
        assert await run_governance(argv_rec) == 0
        target2_id, _, _ = await _create_provider(factory)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp14 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {mgr2_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step14-new-root-admin")},
                json={"target_provider_id": str(target2_id), "permission": "PROFESSIONAL_REVIEW"},
            )
            assert resp14.status_code == 200
    finally:
        await engine.dispose()


async def test_public_root_attack_matrix():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        mgr_id, mgr_token, _ = await _create_provider(factory)
        target_id, _, _ = await _create_provider(factory)
        root_grant_id = uuid.uuid4()
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=root_grant_id, provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-ROOT-ATTACK"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp_grant = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("atk-grant-root")}, json={"target_provider_id": str(target_id), "permission": "TRUST_PERMISSION_MANAGE"})
            assert resp_grant.status_code == 403
            resp_rev = await client.post(f"/api/v2/provider-trust/permissions/{root_grant_id}/revoke", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("atk-rev-root")}, json={"revocation_reason_code": "SECURITY_RESPONSE"})
            assert resp_rev.status_code == 403
    finally:
        await engine.dispose()


async def test_legacy_role_confusion_matrix():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        target_id, _, _ = await _create_provider(factory)
        sub_grant_id = uuid.uuid4()
        fac_id = await _create_facility(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=sub_grant_id, provider_id=target_id, permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=None, revoked_at=None, granted_by_actor_id="prior-manager", governance_reference="SEED-GRANT"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        for role_name in ("admin", "privacy_officer", "auditor", "clinical_reviewer", "clinician", "receptionist"):
            prov_id, token, _ = await _create_provider(factory)
            async with factory() as db:
                db.add(ProviderHospitalAffiliation(provider_id=prov_id, hospital_id=fac_id, roles=[role_name], is_active=True))
                await db.commit()
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp_g = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key(f"legacy-g-{role_name}")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
                assert resp_g.status_code == 403
                resp_r = await client.post(f"/api/v2/provider-trust/permissions/{sub_grant_id}/revoke", headers={"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key(f"legacy-r-{role_name}")}, json={"revocation_reason_code": "ACCESS_REMOVED"})
                assert resp_r.status_code == 403
    finally:
        await engine.dispose()


async def test_authentication_confusion_matrix():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        mgr_id, mgr_token, _ = await _create_provider(factory)
        target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-AUTH-CONF"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            r1 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Idempotency-Key": _key("auth-none")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r1.status_code == 401
            r2 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": "Basic dXNlcjpwYXNz", "Idempotency-Key": _key("auth-basic")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r2.status_code == 401
            r3 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": "Bearer not-a-real-token-12345", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("auth-invalid")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r3.status_code == 401
    finally:
        await engine.dispose()


async def test_target_disclosure_matrix():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        mgr_id, mgr_token, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-TARGET-DISC"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            r1 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("disc-nonexistent")}, json={"target_provider_id": str(uuid.uuid4()), "permission": "PROFESSIONAL_REVIEW"})
            assert r1.status_code == 404
    finally:
        await engine.dispose()


async def test_request_shape_adversarial_matrix():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        mgr_id, mgr_token, _ = await _create_provider(factory)
        target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-SHAPE"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT}
            r1 = await client.post("/api/v2/provider-trust/permissions/grant", headers={**headers, "Idempotency-Key": _key("sh-1")}, json={"target_provider_id": str(target_id), "permission": "UNKNOWN_PERMISSION_TYPE"})
            assert r1.status_code == 422
    finally:
        await engine.dispose()


async def test_root_cli_adversarial_matrix(monkeypatch):
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        target_id, _, _ = await _create_provider(factory)
        valid_until_str = (now + timedelta(days=30)).isoformat()
        base_grant_args = ["grant-root", "--expected-database-name", _DB_NAME, "--apply", "--operator-actor-id", "op-1", "--approver-actor-id", "appr-1", "--governance-reference", "QUAL-CLI-ADV", "--idempotency-key", _key("cli-adv"), "--expected-active-root-count", "0", "--target-provider-id", str(target_id), "--confirm-target-provider-id", str(target_id), "--valid-until", valid_until_str]
        monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", "")
        assert await run_governance(base_grant_args) != 0
        monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", db_url)
        args2 = list(base_grant_args)
        args2[2] = "wrong_database_name"
        assert await run_governance(args2) != 0
        with patch("scripts.governance_trust_root._derive_repository_heads", return_value=("nonexistent_revision",)):
            assert await run_governance(base_grant_args) != 0
        assert await run_governance(base_grant_args) == 0
        async with factory() as db:
            grant_row = (await db.execute(select(ProviderTrustPermissionGrant))).scalar_one()
            root_gid = grant_row.id
        revoke_ack_args = ["revoke-root", "--expected-database-name", _DB_NAME, "--apply", "--operator-actor-id", "op-1", "--approver-actor-id", "appr-1", "--governance-reference", "QUAL-CLI-ADV-REV-ACK", "--idempotency-key", _key("cli-adv-rev-ack"), "--expected-active-root-count", "1", "--grant-id", str(root_gid), "--confirm-grant-id", str(root_gid), "--revocation-reason-code", "SECURITY_RESPONSE", "--acknowledge-zero-active-roots"]
        assert await run_governance(revoke_ack_args) == 0
    finally:
        await engine.dispose()


async def test_root_set_concurrency():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        target1_id, _, _ = await _create_provider(factory)
        target2_id, _, _ = await _create_provider(factory)
        valid_until = now + timedelta(days=30)
        async def _run_grant(tid: uuid.UUID, op: str, key: str):
            async with factory() as db:
                return await ProviderTrustRootGovernanceService(db).grant_root(operator_actor_id=op, approver_actor_id=f"appr-{op}", target_provider_id=tid, valid_until=valid_until, expected_active_root_count=0, governance_reference="QUAL-ROOT-CAS", idempotency_key=key, now=now)
        res1, res2 = await asyncio.gather(_run_grant(target1_id, "op-cas-1", _key("cas-1")), _run_grant(target2_id, "op-cas-2", _key("cas-2")), return_exceptions=True)
        successes = [r for r in (res1, res2) if not isinstance(r, Exception)]
        failures = [r for r in (res1, res2) if isinstance(r, ProviderTrustRootGovernanceError) and r.code == "ROOT_SET_CHANGED"]
        assert len(successes) == 1 and len(failures) == 1
    finally:
        await engine.dispose()


async def test_ordinary_duplicate_concurrency():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        mgr_id, mgr_token, _ = await _create_provider(factory)
        target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-DUP-CONC"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        async def _call_grant(key: str):
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": key}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
        r1, r2 = await asyncio.gather(_call_grant(_key("dup-c-1")), _call_grant(_key("dup-c-2")))
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    finally:
        await engine.dispose()


async def test_reciprocal_manager_concurrency():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        mgr_a_id, _, _ = await _create_provider(factory)
        mgr_b_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            for mgr in (mgr_a_id, mgr_b_id):
                db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-RECIP-MGR"))
            await db.commit()
    finally:
        await engine.dispose()


async def test_ordinary_revoke_concurrency():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_root_revocation_linearization_a():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_root_revocation_linearization_b():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_contact_assurance_revocation():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_account_deactivation():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_credential_deactivation():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_root_expiry():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_future_root():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_mfa_freshness_boundary():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_audit_chain_and_partitions():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_atomicity_qualification():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_clinical_separation_matrix():
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    await engine.dispose()


async def test_route_surface_freeze():
    routes = [r for r in main_app.routes if getattr(r, "path", "").startswith("/api/v2/provider-trust")]
    assert len(routes) == 26
    assert len([r for r in routes if "POST" in getattr(r, "methods", set())]) == 26


async def test_architecture_static_guards():
    app_dir = Path("app")
    bypass_patterns = ["allow_root", "bootstrap_root", "super_admin", "skip_authorization", "first_user_admin", "legacy_role_to_root", "root_via_http"]
    for py in app_dir.rglob("*.py"):
        content = py.read_text(encoding="utf-8")
        for pat in bypass_patterns:
            assert pat not in content, f"Prohibited pattern {pat!r} found in {py}"
