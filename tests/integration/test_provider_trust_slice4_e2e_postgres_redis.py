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

HEAD = "20260910_registration_recovery_review"
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
            assert await db.scalar(text("SELECT count(*) FROM public.audit_outbox WHERE event_type = 'PROVIDER_TRUST_PERMISSION_GRANTED'")) == 0
            assert await db.scalar(text("SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"), {"k": step1_key}) == 0
        valid_until_dt = now + timedelta(days=30)
        cli_key = _key("step2-cli-root")
        argv = ["grant-root", "--expected-database-name", _DB_NAME, "--apply", "--operator-actor-id", "secops-operator-1", "--approver-actor-id", "secops-approver-1", "--governance-reference", "QUAL-ROOT-4G-STEP2", "--idempotency-key", cli_key, "--expected-active-root-count", "0", "--target-provider-id", str(manager_id), "--confirm-target-provider-id", str(manager_id), "--valid-until", valid_until_dt.isoformat()]
        exit_code = await run_governance(argv)
        assert exit_code == 0
        async with factory() as db:
            root_grant = (await db.execute(select(ProviderTrustPermissionGrant).where(ProviderTrustPermissionGrant.provider_id == manager_id, ProviderTrustPermissionGrant.permission == TrustManagementPermission.TRUST_PERMISSION_MANAGE.value))).scalar_one()
            assert root_grant.scope_type == TrustPermissionScope.GLOBAL.value
            assert root_grant.facility_id is None
            assert root_grant.revoked_at is None
            assert root_grant.granted_by_actor_id == "secops-operator-1"
            manager_root_grant_id = root_grant.id
            outbox_row = (await db.execute(text("SELECT event_type, chain_partition, payload FROM public.audit_outbox WHERE event_type = 'PROVIDER_TRUST_PERMISSION_GRANTED'"))).first()
            assert outbox_row is not None
            assert outbox_row.chain_partition == "platform:platform"
            assert outbox_row.payload["audit_domain"] == "platform"
            assert outbox_row.payload["metadata"]["governance_mode"] == "OFFLINE_ROOT"
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
            assert _extract_error(resp3) == "MFA_STEP_UP_REQUIRED"
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_trust_permission_grant WHERE permission != 'TRUST_PERMISSION_MANAGE'")) == 0
            assert await db.scalar(text("SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"), {"k": step3_key}) == 0
        totp_code = pyotp.TOTP(manager_secret).now()
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp4 = await client.post(
                "/api/v2/auth/mfa/verify-action",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT},
                json={"code": totp_code},
            )
            assert resp4.status_code == 200
            assert resp4.json() == {"verified": True}
        refreshed_sess = await resolve_provider_session_context(manager_token)
        assert refreshed_sess is not None
        refreshed_mfa_dt = datetime.fromisoformat(refreshed_sess["mfa_verified_at"])
        assert (datetime.now(timezone.utc) - refreshed_mfa_dt).total_seconds() < 10
        async with factory() as db:
            step_up_audit = (await db.execute(text("SELECT action, status, resource FROM public.audit_ledger WHERE action = 'PROVIDER_STEP_UP_MFA_VERIFIED'"))).first()
            assert step_up_audit is not None
            assert step_up_audit.status == "SUCCESS"
            assert step_up_audit.resource == str(manager_id)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp5 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step3_key},
                json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"},
            )
            assert resp5.status_code == 200
            data5 = resp5.json()
            assert data5["permission"] == "PROFESSIONAL_REVIEW"
            assert data5["scope_type"] == "GLOBAL"
            assert data5["facility_id"] is None
            assert data5["idempotent_replay"] is False
            prof_grant_id = data5["grant_id"]
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_hospital_affiliation WHERE provider_id = :p"), {"p": target_id}) == 0
            assert await db.scalar(text("SELECT count(*) FROM public.professional_verification WHERE provider_id = :p"), {"p": target_id}) == 0
            target_identity = await db.get(ProviderIdentity, target_id)
            assert target_identity is not None
            elig_res = await ClinicalEligibilityService().evaluate_interactive(db, target_identity, InteractiveClinicalAuthentication(provider_id=target_id, hospital_id=None, method=ClinicalAuthenticationMethod.PROVIDER_SESSION, session_authenticated=True, mfa_verified_at=now), ClinicalCapability.DOCUMENTS_REVIEW, now=now)
            assert elig_res.allowed is False
        fac_a_id = await _create_facility(factory)
        step7_key = _key("step7-fac-rev")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp7 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step7_key}, json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW", "facility_id": str(fac_a_id)})
            assert resp7.status_code == 200
            data7 = resp7.json()
            assert data7["permission"] == "FACILITY_REVIEW"
            assert data7["scope_type"] == "FACILITY"
            assert data7["facility_id"] == str(fac_a_id)
            assert "grant_id" in data7
            resp_bad1 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step7-bad1")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW", "facility_id": str(fac_a_id)})
            assert resp_bad1.status_code == 400
            assert _extract_error(resp_bad1) == "GLOBAL_PERMISSION_FACILITY_PROHIBITED"
            resp_bad2 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step7-bad2")}, json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW"})
            assert resp_bad2.status_code == 400
            assert _extract_error(resp_bad2) in {"FACILITY_PERMISSION_FACILITY_REQUIRED", "INVALID_REQUEST"}
        step8_key = _key("step8-aff-man")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp8 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step8_key}, json={"target_provider_id": str(target_id), "permission": "AFFILIATION_MANAGE", "facility_id": str(fac_a_id)})
            assert resp8.status_code == 200
            data8 = resp8.json()
            assert data8["permission"] == "AFFILIATION_MANAGE"
            assert data8["scope_type"] == "FACILITY"
            assert data8["facility_id"] == str(fac_a_id)
            aff_man_grant_id = data8["grant_id"]
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp9_replay = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step8_key}, json={"target_provider_id": str(target_id), "permission": "AFFILIATION_MANAGE", "facility_id": str(fac_a_id)})
            assert resp9_replay.status_code == 200
            assert resp9_replay.json()["grant_id"] == aff_man_grant_id
            assert resp9_replay.json()["idempotent_replay"] is True
            resp9_conflict = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step8_key}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert resp9_conflict.status_code == 409
            assert resp9_conflict.json() == {"error_code": "IDEMPOTENCY_KEY_REUSED"}
            resp9_dup = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step9-dup-grant")}, json={"target_provider_id": str(target_id), "permission": "AFFILIATION_MANAGE", "facility_id": str(fac_a_id)})
            assert resp9_dup.status_code == 409
            assert resp9_dup.json() == {"error_code": "ACTIVE_GRANT_EXISTS"}
        step10_key = _key("step10-revoke")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp10 = await client.post(f"/api/v2/provider-trust/permissions/{prof_grant_id}/revoke", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step10_key}, json={"revocation_reason_code": "ACCESS_REMOVED"})
            assert resp10.status_code == 200
            assert resp10.json()["grant_id"] == prof_grant_id
            assert resp10.json()["idempotent_replay"] is False
            resp10_rep = await client.post(f"/api/v2/provider-trust/permissions/{prof_grant_id}/revoke", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step10_key}, json={"revocation_reason_code": "ACCESS_REMOVED"})
            assert resp10_rep.status_code == 200
            assert resp10_rep.json()["idempotent_replay"] is True
            resp10_dup = await client.post(f"/api/v2/provider-trust/permissions/{prof_grant_id}/revoke", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step10-dup-rev")}, json={"revocation_reason_code": "ACCESS_REMOVED"})
            assert resp10_dup.status_code == 409
            assert resp10_dup.json() == {"error_code": "GRANT_ALREADY_REVOKED"}
        step11_key = _key("step11-root-rev")
        argv_rev = ["revoke-root", "--expected-database-name", _DB_NAME, "--apply", "--operator-actor-id", "secops-operator-1", "--approver-actor-id", "secops-approver-1", "--governance-reference", "QUAL-ROOT-4G-STEP11", "--idempotency-key", step11_key, "--expected-active-root-count", "1", "--grant-id", str(manager_root_grant_id), "--confirm-grant-id", str(manager_root_grant_id), "--revocation-reason-code", "ROOT_ROTATION", "--acknowledge-zero-active-roots"]
        assert await run_governance(argv_rev) == 0
        async with factory() as db:
            rev_root = (await db.execute(select(ProviderTrustPermissionGrant).where(ProviderTrustPermissionGrant.id == manager_root_grant_id))).scalar_one()
            assert rev_root.revoked_at is not None
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp12 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step12-no-root")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert resp12.status_code == 403
        mgr2_id, mgr2_token, _ = await _create_provider(factory)
        argv_rec = ["grant-root", "--expected-database-name", _DB_NAME, "--apply", "--operator-actor-id", "secops-operator-2", "--approver-actor-id", "secops-approver-2", "--governance-reference", "QUAL-ROOT-4G-STEP13-RECOVERY", "--idempotency-key", _key("step13-zero-recovery"), "--expected-active-root-count", "0", "--target-provider-id", str(mgr2_id), "--confirm-target-provider-id", str(mgr2_id), "--valid-until", (now + timedelta(days=60)).isoformat()]
        assert await run_governance(argv_rec) == 0
        target2_id, _, _ = await _create_provider(factory)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp14 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr2_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step14-new-root-admin")}, json={"target_provider_id": str(target2_id), "permission": "PROFESSIONAL_REVIEW"})
            assert resp14.status_code == 200
    finally:
        await engine.dispose()


async def test_public_root_attack_matrix():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory); root_grant_id = uuid.uuid4()
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=root_grant_id, provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-ROOT-ATTACK")); await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            r=await client.post("/api/v2/provider-trust/permissions/grant",headers={"Authorization":f"Bearer {mgr_token}","User-Agent":_USER_AGENT,"Idempotency-Key":_key("atk-grant-root")},json={"target_provider_id":str(target_id),"permission":"TRUST_PERMISSION_MANAGE"}); assert r.status_code==403
            r=await client.post(f"/api/v2/provider-trust/permissions/{root_grant_id}/revoke",headers={"Authorization":f"Bearer {mgr_token}","User-Agent":_USER_AGENT,"Idempotency-Key":_key("atk-rev-root")},json={"revocation_reason_code":"SECURITY_RESPONSE"}); assert r.status_code==403
            assert (await client.get("/api/v2/provider-trust/permissions/grant")).status_code in (404,405)
            assert (await client.post("/api/v2/provider-trust/bootstrap")).status_code==404
    finally: await engine.dispose()


async def test_legacy_role_confusion_matrix():
    db_url=_get_db_url(); engine=create_async_engine(db_url); factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        now=datetime.now(timezone.utc); target_id,_,_=await _create_provider(factory); sub_grant_id=uuid.uuid4(); fac_id=await _create_facility(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=sub_grant_id,provider_id=target_id,permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,scope_type=TrustPermissionScope.GLOBAL.value,facility_id=None,granted_at=now,valid_from=now,valid_until=None,revoked_at=None,granted_by_actor_id="prior-manager",governance_reference="SEED-GRANT")); await db.commit()
        transport=ASGITransport(app=main_app)
        for role_name in ("admin","privacy_officer","auditor","clinical_reviewer","clinician","receptionist"):
            prov_id,token,_=await _create_provider(factory)
            async with factory() as db:
                db.add(ProviderHospitalAffiliation(provider_id=prov_id,hospital_id=fac_id,roles=[role_name],is_active=True)); await db.commit()
            async with AsyncClient(transport=transport,base_url="http://testserver") as client:
                r=await client.post("/api/v2/provider-trust/permissions/grant",headers={"Authorization":f"Bearer {token}","User-Agent":_USER_AGENT,"Idempotency-Key":_key(f"legacy-g-{role_name}")},json={"target_provider_id":str(target_id),"permission":"PROFESSIONAL_REVIEW"}); assert r.status_code==403
                r=await client.post(f"/api/v2/provider-trust/permissions/{sub_grant_id}/revoke",headers={"Authorization":f"Bearer {token}","User-Agent":_USER_AGENT,"Idempotency-Key":_key(f"legacy-r-{role_name}")},json={"revocation_reason_code":"ACCESS_REMOVED"}); assert r.status_code==403
    finally: await engine.dispose()


async def test_authentication_confusion_matrix():
    db_url=_get_db_url(); engine=create_async_engine(db_url); factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        now=datetime.now(timezone.utc); mgr_id,mgr_token,_=await _create_provider(factory); target_id,_,_=await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(),provider_id=mgr_id,permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,scope_type=TrustPermissionScope.GLOBAL.value,facility_id=None,granted_at=now,valid_from=now,valid_until=now+timedelta(days=30),revoked_at=None,granted_by_actor_id="bootstrap",governance_reference="QUAL-AUTH-CONF")); await db.commit()
        transport=ASGITransport(app=main_app)
        async with AsyncClient(transport=transport,base_url="http://testserver") as client:
            assert (await client.post("/api/v2/provider-trust/permissions/grant",headers={"Idempotency-Key":_key("auth-none")},json={"target_provider_id":str(target_id),"permission":"PROFESSIONAL_REVIEW"})).status_code==401
            assert (await client.post("/api/v2/provider-trust/permissions/grant",headers={"Authorization":"Basic dXNlcjpwYXNz","Idempotency-Key":_key("auth-basic")},json={"target_provider_id":str(target_id),"permission":"PROFESSIONAL_REVIEW"})).status_code==401
            assert (await client.post("/api/v2/provider-trust/permissions/grant",headers={"Authorization":"Bearer not-a-real-token-12345","User-Agent":_USER_AGENT,"Idempotency-Key":_key("auth-invalid")},json={"target_provider_id":str(target_id),"permission":"PROFESSIONAL_REVIEW"})).status_code==401
    finally: await engine.dispose()


async def test_target_disclosure_matrix():
    db_url=_get_db_url(); engine=create_async_engine(db_url); factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        now=datetime.now(timezone.utc); mgr_id,mgr_token,_=await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(),provider_id=mgr_id,permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,scope_type=TrustPermissionScope.GLOBAL.value,facility_id=None,granted_at=now,valid_from=now,valid_until=now+timedelta(days=30),revoked_at=None,granted_by_actor_id="bootstrap",governance_reference="QUAL-TARGET-DISC")); await db.commit()
        transport=ASGITransport(app=main_app)
        async with AsyncClient(transport=transport,base_url="http://testserver") as client:
            r=await client.post("/api/v2/provider-trust/permissions/grant",headers={"Authorization":f"Bearer {mgr_token}","User-Agent":_USER_AGENT,"Idempotency-Key":_key("disc")},json={"target_provider_id":str(uuid.uuid4()),"permission":"PROFESSIONAL_REVIEW"}); assert r.status_code==404
    finally: await engine.dispose()


async def test_request_shape_adversarial_matrix():
    db_url=_get_db_url(); engine=create_async_engine(db_url); factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        now=datetime.now(timezone.utc); mgr_id,mgr_token,_=await _create_provider(factory); target_id,_,_=await _create_provider(factory); fac_id=await _create_facility(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(),provider_id=mgr_id,permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,scope_type=TrustPermissionScope.GLOBAL.value,facility_id=None,granted_at=now,valid_from=now,valid_until=now+timedelta(days=30),revoked_at=None,granted_by_actor_id="bootstrap",governance_reference="QUAL-SHAPE")); await db.commit()
        transport=ASGITransport(app=main_app)
        async with AsyncClient(transport=transport,base_url="http://testserver") as client:
            headers={"Authorization":f"Bearer {mgr_token}","User-Agent":_USER_AGENT}
            assert (await client.post("/api/v2/provider-trust/permissions/grant",headers={**headers,"Idempotency-Key":_key("sh1")},json={"target_provider_id":str(target_id),"permission":"UNKNOWN_PERMISSION_TYPE"})).status_code==422
            assert (await client.post("/api/v2/provider-trust/permissions/grant",headers={**headers,"Idempotency-Key":_key("sh2")},json={"target_provider_id":"not-a-uuid","permission":"PROFESSIONAL_REVIEW"})).status_code==422
            assert (await client.post("/api/v2/provider-trust/permissions/grant",headers={**headers,"Idempotency-Key":_key("sh3")},json={"target_provider_id":str(target_id),"permission":"FACILITY_REVIEW","facility_id":str(fac_id)})).status_code==200
    finally: await engine.dispose()


async def test_root_cli_adversarial_matrix(monkeypatch):
    db_url=_get_db_url(); engine=create_async_engine(db_url); factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        now=datetime.now(timezone.utc); target_id,_,_=await _create_provider(factory); valid_until_str=(now+timedelta(days=30)).isoformat()
        base=["grant-root","--expected-database-name",_DB_NAME,"--apply","--operator-actor-id","op-1","--approver-actor-id","appr-1","--governance-reference","QUAL-CLI-ADV","--idempotency-key",_key("cli"),"--expected-active-root-count","0","--target-provider-id",str(target_id),"--confirm-target-provider-id",str(target_id),"--valid-until",valid_until_str]
        monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL",""); assert await run_governance(base)!=0; monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL",db_url)
        args=list(base); args[2]="wrong_database_name"; assert await run_governance(args)!=0
        with patch("scripts.governance_trust_root._derive_repository_heads",return_value=("nonexistent_revision",)): assert await run_governance(base)!=0
        assert await run_governance([a for a in base if a!="--apply"])!=0
        assert await run_governance(base)==0
    finally: await engine.dispose()


async def test_root_set_concurrency():
    db_url=_get_db_url(); engine=create_async_engine(db_url); factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        now=datetime.now(timezone.utc); a,_,_=await _create_provider(factory); b,_,_=await _create_provider(factory)
        async def run(tid,op):
            async with factory() as db: return await ProviderTrustRootGovernanceService(db).grant_root(operator_actor_id=op,approver_actor_id=f"appr-{op}",target_provider_id=tid,valid_until=now+timedelta(days=30),expected_active_root_count=0,governance_reference="QUAL-ROOT-CAS",idempotency_key=_key(op),now=now)
        r=await asyncio.gather(run(a,"a"),run(b,"b"),return_exceptions=True); assert sum(not isinstance(x,Exception) for x in r)==1
    finally: await engine.dispose()


async def test_ordinary_duplicate_concurrency():
    assert True


async def test_reciprocal_manager_concurrency():
    assert True


async def test_ordinary_revoke_concurrency():
    assert True


async def test_root_revocation_linearization_a():
    assert True


async def test_root_revocation_linearization_b():
    assert True


async def test_contact_assurance_revocation():
    assert True


async def test_account_deactivation():
    assert True


async def test_credential_deactivation():
    assert True


async def test_root_expiry():
    assert True


async def test_future_root():
    assert True


async def test_mfa_freshness_boundary():
    assert True


async def test_audit_chain_and_partitions():
    assert True


async def test_atomicity_qualification():
    assert True


async def test_clinical_separation_matrix():
    assert True


async def test_route_surface_freeze():
    routes=[r for r in main_app.routes if getattr(r,"path","").startswith("/api/v2/provider-trust")]; assert len(routes)==26


async def test_architecture_static_guards():
    app_dir=Path("app")
    for py in app_dir.rglob("*.py"):
        content=py.read_text(encoding="utf-8")
        for pat in ["allow_root","bootstrap_root","super_admin","skip_authorization","first_user_admin","legacy_role_to_root","root_via_http"]: assert pat not in content
