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

HEAD = "20260909_device_trust_lifecycle"
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
    """Create ProviderIdentity, ProviderCredential, and an active Redis session."""
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
                headers={
                    "Authorization": f"Bearer {manager_token}",
                    "User-Agent": _USER_AGENT,
                    "Idempotency-Key": step1_key,
                },
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
        argv = [
            "grant-root", "--expected-database-name", _DB_NAME, "--apply",
            "--operator-actor-id", "secops-operator-1", "--approver-actor-id", "secops-approver-1",
            "--governance-reference", "QUAL-ROOT-4G-STEP2", "--idempotency-key", cli_key,
            "--expected-active-root-count", "0", "--target-provider-id", str(manager_id),
            "--confirm-target-provider-id", str(manager_id), "--valid-until", valid_until_dt.isoformat(),
        ]
        assert await run_governance(argv) == 0

        async with factory() as db:
            root_grant = (
                await db.execute(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == manager_id,
                        ProviderTrustPermissionGrant.permission == TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                    )
                )
            ).scalar_one()
            assert root_grant.scope_type == TrustPermissionScope.GLOBAL.value
            assert root_grant.facility_id is None
            assert root_grant.revoked_at is None
            assert root_grant.granted_by_actor_id == "secops-operator-1"
            manager_root_grant_id = root_grant.id
            outbox_row = (
                await db.execute(
                    text("SELECT event_type, chain_partition, payload FROM public.audit_outbox WHERE event_type = 'PROVIDER_TRUST_PERMISSION_GRANTED'")
                )
            ).first()
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
            step_up_audit = (
                await db.execute(text("SELECT action, status, resource FROM public.audit_ledger WHERE action = 'PROVIDER_STEP_UP_MFA_VERIFIED'"))
            ).first()
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
            eligibility_svc = ClinicalEligibilityService()
            clin_auth = InteractiveClinicalAuthentication(
                provider_id=target_id,
                hospital_id=None,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=now,
            )
            elig_res = await eligibility_svc.evaluate_interactive(
                db, target_identity, clin_auth, ClinicalCapability.DOCUMENTS_REVIEW, now=now
            )
            assert elig_res.allowed is False

        fac_a_id = await _create_facility(factory)
        step7_key = _key("step7-fac-rev")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp7 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step7_key},
                json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW", "facility_id": str(fac_a_id)},
            )
            assert resp7.status_code == 200
            data7 = resp7.json()
            assert data7["permission"] == "FACILITY_REVIEW"
            assert data7["scope_type"] == "FACILITY"
            assert data7["facility_id"] == str(fac_a_id)
            assert "grant_id" in data7
            resp_bad1 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step7-bad1")},
                json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW", "facility_id": str(fac_a_id)},
            )
            assert resp_bad1.status_code == 400
            assert _extract_error(resp_bad1) == "GLOBAL_PERMISSION_FACILITY_PROHIBITED"
            resp_bad2 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step7-bad2")},
                json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW"},
            )
            assert resp_bad2.status_code == 400
            assert _extract_error(resp_bad2) in {"FACILITY_PERMISSION_FACILITY_REQUIRED", "INVALID_REQUEST"}

        step8_key = _key("step8-aff-man")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp8 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step8_key},
                json={"target_provider_id": str(target_id), "permission": "AFFILIATION_MANAGE", "facility_id": str(fac_a_id)},
            )
            assert resp8.status_code == 200
            data8 = resp8.json()
            assert data8["permission"] == "AFFILIATION_MANAGE"
            assert data8["scope_type"] == "FACILITY"
            assert data8["facility_id"] == str(fac_a_id)
            aff_man_grant_id = data8["grant_id"]

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp9_replay = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step8_key},
                json={"target_provider_id": str(target_id), "permission": "AFFILIATION_MANAGE", "facility_id": str(fac_a_id)},
            )
            assert resp9_replay.status_code == 200
            assert resp9_replay.json()["grant_id"] == aff_man_grant_id
            assert resp9_replay.json()["idempotent_replay"] is True
            resp9_conflict = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step8_key},
                json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"},
            )
            assert resp9_conflict.status_code == 409
            assert resp9_conflict.json() == {"error_code": "IDEMPOTENCY_KEY_REUSED"}
            resp9_dup = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step9-dup-grant")},
                json={"target_provider_id": str(target_id), "permission": "AFFILIATION_MANAGE", "facility_id": str(fac_a_id)},
            )
            assert resp9_dup.status_code == 409
            assert resp9_dup.json() == {"error_code": "ACTIVE_GRANT_EXISTS"}

        step10_key = _key("step10-revoke")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp10 = await client.post(
                f"/api/v2/provider-trust/permissions/{prof_grant_id}/revoke",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step10_key},
                json={"revocation_reason_code": "ACCESS_REMOVED"},
            )
            assert resp10.status_code == 200
            assert resp10.json()["grant_id"] == prof_grant_id
            assert resp10.json()["idempotent_replay"] is False
            resp10_rep = await client.post(
                f"/api/v2/provider-trust/permissions/{prof_grant_id}/revoke",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": step10_key},
                json={"revocation_reason_code": "ACCESS_REMOVED"},
            )
            assert resp10_rep.status_code == 200
            assert resp10_rep.json()["idempotent_replay"] is True
            resp10_dup = await client.post(
                f"/api/v2/provider-trust/permissions/{prof_grant_id}/revoke",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step10-dup-rev")},
                json={"revocation_reason_code": "ACCESS_REMOVED"},
            )
            assert resp10_dup.status_code == 409
            assert resp10_dup.json() == {"error_code": "GRANT_ALREADY_REVOKED"}

        step11_key = _key("step11-root-rev")
        argv_rev = [
            "revoke-root", "--expected-database-name", _DB_NAME, "--apply",
            "--operator-actor-id", "secops-operator-1", "--approver-actor-id", "secops-approver-1",
            "--governance-reference", "QUAL-ROOT-4G-STEP11", "--idempotency-key", step11_key,
            "--expected-active-root-count", "1", "--grant-id", str(manager_root_grant_id),
            "--confirm-grant-id", str(manager_root_grant_id), "--revocation-reason-code", "ROOT_ROTATION",
            "--acknowledge-zero-active-roots",
        ]
        assert await run_governance(argv_rev) == 0

        async with factory() as db:
            rev_root = (
                await db.execute(select(ProviderTrustPermissionGrant).where(ProviderTrustPermissionGrant.id == manager_root_grant_id))
            ).scalar_one()
            assert rev_root.revoked_at is not None
            rev_audit = (
                await db.execute(
                    text("SELECT event_type, chain_partition, payload FROM public.audit_outbox WHERE event_type = 'PROVIDER_TRUST_PERMISSION_REVOKED' AND payload->>'target_id' = :gid"),
                    {"gid": str(manager_root_grant_id)},
                )
            ).first()
            assert rev_audit is not None
            assert rev_audit.chain_partition == "platform:platform"
            assert rev_audit.payload["audit_domain"] == "platform"
            assert rev_audit.payload["metadata"]["governance_mode"] == "OFFLINE_ROOT"

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp12 = await client.post(
                "/api/v2/provider-trust/permissions/grant",
                headers={"Authorization": f"Bearer {manager_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("step12-no-root")},
                json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"},
            )
            assert resp12.status_code == 403
            assert resp12.json() == {"error_code": "AUTHORIZATION_DENIED"}

        mgr2_id, mgr2_token, _ = await _create_provider(factory)
        step13_key = _key("step13-zero-recovery")
        argv_rec = [
            "grant-root", "--expected-database-name", _DB_NAME, "--apply",
            "--operator-actor-id", "secops-operator-2", "--approver-actor-id", "secops-approver-2",
            "--governance-reference", "QUAL-ROOT-4G-STEP13-RECOVERY", "--idempotency-key", step13_key,
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
            assert resp14.json()["permission"] == "PROFESSIONAL_REVIEW"
            assert resp14.json()["idempotent_replay"] is False
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
            assert resp_grant.json() == {"error_code": "ROOT_PERMISSION_OFFLINE_ONLY"}
            resp_rev = await client.post(f"/api/v2/provider-trust/permissions/{root_grant_id}/revoke", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("atk-rev-root")}, json={"revocation_reason_code": "SECURITY_RESPONSE"})
            assert resp_rev.status_code == 403
            assert resp_rev.json() == {"error_code": "ROOT_PERMISSION_OFFLINE_ONLY"}
            assert (await client.get("/api/v2/provider-trust/permissions/grant")).status_code in (404, 405)
            assert (await client.patch(f"/api/v2/provider-trust/permissions/{root_grant_id}/revoke")).status_code in (404, 405)
            assert (await client.delete(f"/api/v2/provider-trust/permissions/{root_grant_id}")).status_code in (404, 405)
            assert (await client.post("/api/v2/provider-trust/bootstrap")).status_code == 404
            assert (await client.post("/api/v2/provider-trust/root/bootstrap")).status_code == 404
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
                assert resp_g.json() == {"error_code": "AUTHORIZATION_DENIED"}
                resp_r = await client.post(f"/api/v2/provider-trust/permissions/{sub_grant_id}/revoke", headers={"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key(f"legacy-r-{role_name}")}, json={"revocation_reason_code": "ACCESS_REMOVED"})
                assert resp_r.status_code == 403
                assert resp_r.json() == {"error_code": "AUTHORIZATION_DENIED"}
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
            assert _extract_error(r1) in {"AUTHENTICATION_REQUIRED", "PROVIDER_SESSION_REQUIRED"}
            r2 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": "Basic dXNlcjpwYXNz", "Idempotency-Key": _key("auth-basic")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r2.status_code == 401
            r3 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": "Bearer not-a-real-token-12345", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("auth-invalid")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r3.status_code == 401
            _, exp_token, _ = await _create_provider(factory)
            redis = get_async_redis_client()
            import json
            exp_sess = await resolve_provider_session_context(exp_token)
            assert exp_sess is not None
            exp_sess["expires_at"] = (now - timedelta(hours=1)).isoformat()
            await redis.set(f"provider_session:{exp_token}", json.dumps(exp_sess))
            r4 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {exp_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("auth-exp")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r4.status_code == 401
            r5 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": "Attacker-Agent/2.0", "Idempotency-Key": _key("auth-ua")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r5.status_code == 401
            malformed_token = "malformed-session-token-999"
            await redis.set(f"provider_session:{malformed_token}", "not-valid-json{{")
            r6 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {malformed_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("auth-malf")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r6.status_code == 401
            stale_mgr_id, stale_token, _ = await _create_provider(factory, session_mfa_time=now - timedelta(minutes=16))
            async with factory() as db:
                db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=stale_mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-AUTH-STALE"))
                await db.commit()
            r7 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {stale_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("auth-stale")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r7.status_code == 428
            r8 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "X-Forwarded-For": "10.0.0.99", "Idempotency-Key": _key("auth-ip-rot")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r8.status_code == 200
            no_mfa_token = await issue_provider_session_token(provider_id=mgr_id, user_agent=_USER_AGENT, client_ip=_CLIENT_IP, mfa_verified_at=None)
            r9 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {no_mfa_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("auth-no-mfa")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r9.status_code == 428
            _, noroot_token, _ = await _create_provider(factory)
            r10 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {noroot_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("auth-noroot")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r10.status_code == 403
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
            assert r1.status_code == 404 and r1.json() == {"error_code": "TARGET_PROVIDER_UNAVAILABLE"}
            inact_prov_id, _, _ = await _create_provider(factory, is_active=False, status="inactive")
            r2 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("disc-inact-acct")}, json={"target_provider_id": str(inact_prov_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r2.status_code == 404 and r2.json() == {"error_code": "TARGET_PROVIDER_UNAVAILABLE"}
            inact_cred_id, _, _ = await _create_provider(factory, credential_active=False)
            r3 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("disc-inact-cred")}, json={"target_provider_id": str(inact_cred_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r3.status_code == 404 and r3.json() == {"error_code": "TARGET_PROVIDER_UNAVAILABLE"}
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
        fac_id = await _create_facility(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-SHAPE"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT}
            cases = [
                ({"target_provider_id": str(target_id), "permission": "UNKNOWN_PERMISSION_TYPE"}, 422),
                ({"target_provider_id": "not-a-uuid", "permission": "PROFESSIONAL_REVIEW"}, 422),
                ({"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW", "unexpected_extra_field": "injected"}, 422),
            ]
            for idx, (body, expected) in enumerate(cases):
                r = await client.post("/api/v2/provider-trust/permissions/grant", headers={**headers, "Idempotency-Key": _key(f"sh-{idx}")}, json=body)
                assert r.status_code == expected
            r4 = await client.post("/api/v2/provider-trust/permissions/grant", headers=headers, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert r4.status_code == 422
            r5 = await client.post("/api/v2/provider-trust/permissions/grant", headers={**headers, "Idempotency-Key": _key("sh-5")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW", "valid_until": "2026-12-31T23:59:59"})
            assert r5.status_code == 400 and _extract_error(r5) == "INVALID_DATETIME_TIMEZONE"
            r6 = await client.post("/api/v2/provider-trust/permissions/grant", headers={**headers, "Idempotency-Key": _key("sh-6")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW", "facility_id": str(fac_id)})
            assert r6.status_code == 400 and _extract_error(r6) == "GLOBAL_PERMISSION_FACILITY_PROHIBITED"
            r7 = await client.post("/api/v2/provider-trust/permissions/grant", headers={**headers, "Idempotency-Key": _key("sh-7")}, json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW"})
            assert r7.status_code == 400
            r8 = await client.post("/api/v2/provider-trust/permissions/grant", headers={**headers, "Idempotency-Key": _key("sh-8")}, json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW", "facility_id": "not-a-valid-facility-uuid"})
            assert r8.status_code == 422
            r9 = await client.post(f"/api/v2/provider-trust/permissions/{uuid.uuid4()}/revoke", headers={**headers, "Idempotency-Key": _key("sh-9")}, json={"revocation_reason_code": "NOT_A_REASON"})
            assert r9.status_code == 422
            r10 = await client.post(f"/api/v2/provider-trust/permissions/{uuid.uuid4()}/revoke", headers={**headers, "Idempotency-Key": _key("sh-10")}, json={"revocation_reason_code": "EXPIRED_SUPERSEDED"})
            assert r10.status_code == 422
            r11 = await client.post(f"/api/v2/provider-trust/permissions/{uuid.uuid4()}/revoke", headers={**headers, "Idempotency-Key": _key("sh-11")}, json={"revocation_reason_code": "ACCESS_REMOVED", "extra_injected_field": "data"})
            assert r11.status_code == 422
            r12 = await client.post("/api/v2/provider-trust/permissions/not-a-uuid/revoke", headers={**headers, "Idempotency-Key": _key("sh-12")}, json={"revocation_reason_code": "ACCESS_REMOVED"})
            assert r12.status_code == 422
    finally:
        await engine.dispose()


async def test_root_cli_adversarial_matrix(monkeypatch):
    db_url = _get_db_url()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        target_id, _, _ = await _create_provider(factory)
        base_grant_args = [
            "grant-root", "--expected-database-name", _DB_NAME, "--apply",
            "--operator-actor-id", "op-1", "--approver-actor-id", "appr-1",
            "--governance-reference", "QUAL-CLI-ADV", "--idempotency-key", _key("cli-adv"),
            "--expected-active-root-count", "0", "--target-provider-id", str(target_id),
            "--confirm-target-provider-id", str(target_id), "--valid-until", (now + timedelta(days=30)).isoformat(),
        ]
        monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", "")
        assert await run_governance(base_grant_args) != 0
        monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", db_url)
        args2 = list(base_grant_args); args2[2] = "wrong_database_name"; assert await run_governance(args2) != 0
        with patch("scripts.governance_trust_root._derive_repository_heads", return_value=("nonexistent_revision",)):
            assert await run_governance(base_grant_args) != 0
        assert await run_governance([a for a in base_grant_args if a != "--apply"]) != 0
        args5 = list(base_grant_args); args5[16] = str(uuid.uuid4()); assert await run_governance(args5) != 0
        args6 = list(base_grant_args); args6[6] = "op-1"; args6[8] = "op-1"; assert await run_governance(args6) != 0
        args7 = list(base_grant_args); args7[10] = "   "; assert await run_governance(args7) != 0
        args8 = list(base_grant_args); args8[18] = "2026-12-31T23:59:59"; assert await run_governance(args8) != 0
        args9 = list(base_grant_args); args9[18] = (now - timedelta(days=1)).isoformat(); assert await run_governance(args9) != 0
        args10 = list(base_grant_args); args10[18] = (now + timedelta(days=91)).isoformat(); assert await run_governance(args10) != 0
        args11 = list(base_grant_args); args11[12] = "5"; assert await run_governance(args11) != 0
        revoke_mismatch_args = ["revoke-root", "--expected-database-name", _DB_NAME, "--apply", "--operator-actor-id", "op-1", "--approver-actor-id", "appr-1", "--governance-reference", "QUAL-CLI-ADV-REV", "--idempotency-key", _key("cli-adv-rev"), "--expected-active-root-count", "1", "--grant-id", str(uuid.uuid4()), "--confirm-grant-id", str(uuid.uuid4()), "--revocation-reason-code", "SECURITY_RESPONSE"]
        assert await run_governance(revoke_mismatch_args) != 0
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_trust_permission_grant")) == 0
        assert await run_governance(base_grant_args) == 0
        async with factory() as db:
            root_gid = (await db.execute(select(ProviderTrustPermissionGrant))).scalar_one().id
        assert await run_governance(base_grant_args) == 0
        revoke_no_ack_args = ["revoke-root", "--expected-database-name", _DB_NAME, "--apply", "--operator-actor-id", "op-1", "--approver-actor-id", "appr-1", "--governance-reference", "QUAL-CLI-ADV-REV-NOACK", "--idempotency-key", _key("cli-adv-rev-noack"), "--expected-active-root-count", "1", "--grant-id", str(root_gid), "--confirm-grant-id", str(root_gid), "--revocation-reason-code", "SECURITY_RESPONSE"]
        assert await run_governance(revoke_no_ack_args) != 0
        revoke_ack_args = revoke_no_ack_args + ["--acknowledge-zero-active-roots"]
        assert await run_governance(revoke_ack_args) == 0
        async with factory() as db:
            assert (await db.get(ProviderTrustPermissionGrant, root_gid)).revoked_at is not None
    finally:
        await engine.dispose()


async def test_root_set_concurrency():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); target1_id, _, _ = await _create_provider(factory); target2_id, _, _ = await _create_provider(factory); valid_until = now + timedelta(days=30)
        async def _run_grant(tid, op, key):
            async with factory() as db:
                return await ProviderTrustRootGovernanceService(db).grant_root(operator_actor_id=op, approver_actor_id=f"appr-{op}", target_provider_id=tid, valid_until=valid_until, expected_active_root_count=0, governance_reference="QUAL-ROOT-CAS", idempotency_key=key, now=now)
        results = await asyncio.gather(_run_grant(target1_id, "op-cas-1", _key("cas-1")), _run_grant(target2_id, "op-cas-2", _key("cas-2")), return_exceptions=True)
        assert len([r for r in results if not isinstance(r, Exception) and r.command == "GRANT_ROOT"]) == 1
        assert len([r for r in results if isinstance(r, ProviderTrustRootGovernanceError) and r.code == "ROOT_SET_CHANGED"]) == 1
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_trust_permission_grant WHERE revoked_at IS NULL")) == 1
    finally:
        await engine.dispose()


async def test_ordinary_duplicate_concurrency():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-DUP-CONC")); await db.commit()
        transport = ASGITransport(app=main_app)
        async def _call_grant(key):
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": key}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
        r1, r2 = await asyncio.gather(_call_grant(_key("dup-c-1")), _call_grant(_key("dup-c-2")))
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]
        assert (r1 if r1.status_code == 409 else r2).json() == {"error_code": "ACTIVE_GRANT_EXISTS"}
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_trust_permission_grant WHERE permission = 'PROFESSIONAL_REVIEW'")) == 1
    finally:
        await engine.dispose()


async def test_reciprocal_manager_concurrency():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_a_id, mgr_a_token, _ = await _create_provider(factory); mgr_b_id, mgr_b_token, _ = await _create_provider(factory)
        async with factory() as db:
            for mgr in (mgr_a_id, mgr_b_id):
                db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-RECIP-MGR"))
            await db.commit()
        transport = ASGITransport(app=main_app)
        async def _grant(token, target, key):
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT, "Idempotency-Key": key}, json={"target_provider_id": str(target), "permission": "PROFESSIONAL_REVIEW"})
        resp_a, resp_b = await asyncio.gather(_grant(mgr_a_token, mgr_b_id, _key("recip-a2b")), _grant(mgr_b_token, mgr_a_id, _key("recip-b2a")))
        assert resp_a.status_code == 200 and resp_b.status_code == 200
    finally:
        await engine.dispose()


async def test_ordinary_revoke_concurrency():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory); sub_id = uuid.uuid4()
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-REV-CONC"))
            db.add(ProviderTrustPermissionGrant(id=sub_id, provider_id=target_id, permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=None, revoked_at=None, granted_by_actor_id="mgr", governance_reference="QUAL-REV-CONC-SUB")); await db.commit()
        transport = ASGITransport(app=main_app)
        async def _call_revoke(key):
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post(f"/api/v2/provider-trust/permissions/{sub_id}/revoke", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": key}, json={"revocation_reason_code": "ACCESS_REMOVED"})
        r1, r2 = await asyncio.gather(_call_revoke(_key("rev-c-1")), _call_revoke(_key("rev-c-2")))
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]
        assert (r1 if r1.status_code == 409 else r2).json() == {"error_code": "GRANT_ALREADY_REVOKED"}
    finally:
        await engine.dispose()


async def test_root_revocation_linearization_a():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory); root_id = uuid.uuid4()
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=root_id, provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-LIN-A")); await db.commit()
        async with factory() as db:
            rev_res = await ProviderTrustRootGovernanceService(db).revoke_root(operator_actor_id="op1", approver_actor_id="appr1", grant_id=root_id, revocation_reason_code=RootRevocationReasonCode.SECURITY_RESPONSE, expected_active_root_count=1, governance_reference="LIN-A-REV", idempotency_key=_key("lin-a-rev"), acknowledge_zero_active_roots=True, now=now)
            assert rev_res.command == "REVOKE_ROOT"
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("lin-a-grant")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})
            assert resp.status_code == 403 and resp.json() == {"error_code": "AUTHORIZATION_DENIED"}
    finally:
        await engine.dispose()


async def test_root_revocation_linearization_b():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory); target2_id, _, _ = await _create_provider(factory); root_id = uuid.uuid4()
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=root_id, provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-LIN-B")); await db.commit()
        auth_mgr = TrustManagementAuthentication(provider_id=mgr_id, method=ClinicalAuthenticationMethod.PROVIDER_SESSION, session_authenticated=True, mfa_verified_at=now)
        m_holding_lock = asyncio.Event(); allow_m_commit = asyncio.Event(); real_enqueue = app_module.enqueue_audit_event
        async def _sync_enqueue(*args, **kwargs):
            m_holding_lock.set(); await allow_m_commit.wait(); return await real_enqueue(*args, **kwargs)
        async def _run_manager_grant():
            async with factory() as db:
                with patch("app.services.provider_trust_permission_application.enqueue_audit_event", _sync_enqueue):
                    return await ProviderTrustPermissionApplicationService(db).apply_grant(actor_id=mgr_id, authentication=auth_mgr, target_provider_id=target_id, permission=TrustManagementPermission.PROFESSIONAL_REVIEW, idempotency_key=_key("lin-b-sub1"), now=now)
        async def _run_offline_revoke():
            async with factory() as db:
                return await ProviderTrustRootGovernanceService(db).revoke_root(operator_actor_id="op1", approver_actor_id="appr1", grant_id=root_id, revocation_reason_code=RootRevocationReasonCode.SECURITY_RESPONSE, expected_active_root_count=1, governance_reference="LIN-B-REV", idempotency_key=_key("lin-b-rev"), acknowledge_zero_active_roots=True, now=now)
        mgr_task = asyncio.create_task(_run_manager_grant()); await m_holding_lock.wait(); rev_task = asyncio.create_task(_run_offline_revoke()); await asyncio.sleep(0.1); assert not rev_task.done(); allow_m_commit.set(); assert (await mgr_task).command == "GRANT"; assert (await rev_task).command == "REVOKE_ROOT"
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp_next = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("lin-b-sub2")}, json={"target_provider_id": str(target2_id), "permission": "PROFESSIONAL_REVIEW"})
            assert resp_next.status_code == 403 and resp_next.json() == {"error_code": "AUTHORIZATION_DENIED"}
    finally:
        await engine.dispose()


async def test_contact_assurance_revocation():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-CONTACT")); await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with factory() as db:
                await db.execute(text("UPDATE public.provider_identity SET phone_verified_at = NULL WHERE id = :p"), {"p": mgr_id}); await db.commit()
            r1 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("no-phone")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"}); assert r1.status_code == 403
            async with factory() as db:
                await db.execute(text("UPDATE public.provider_identity SET phone_verified_at = :now, email_verified_at = NULL WHERE id = :p"), {"p": mgr_id, "now": now}); await db.commit()
            r2 = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("no-email")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"}); assert r2.status_code == 403
    finally:
        await engine.dispose()


async def test_account_deactivation():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-ACCT-DEACT")); await db.commit(); await db.execute(text("UPDATE public.provider_identity SET is_active = FALSE, status = 'suspended' WHERE id = :p"), {"p": mgr_id}); await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("deact-acct")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"}); assert resp.status_code == 403
    finally:
        await engine.dispose()


async def test_credential_deactivation():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-CRED-DEACT")); await db.commit(); await db.execute(text("UPDATE public.provider_credential SET is_active = FALSE WHERE provider_id = :p"), {"p": mgr_id}); await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("deact-cred")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"}); assert resp.status_code == 403
    finally:
        await engine.dispose()


async def test_root_expiry():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now - timedelta(days=30), valid_from=now - timedelta(days=30), valid_until=now - timedelta(seconds=1), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-EXP-ROOT")); await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("exp-root")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"}); assert resp.status_code == 403
    finally:
        await engine.dispose()


async def test_future_root():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory)
        async with factory() as db:
            db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mgr_id, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now + timedelta(hours=1), valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-FUT-ROOT")); await db.commit()
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("fut-root")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"}); assert resp.status_code == 403
    finally:
        await engine.dispose()


async def test_mfa_freshness_boundary():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); transport = ASGITransport(app=main_app)
        async def _test_session_mfa(mfa_time, expected_status):
            mid, mtoken, _ = await _create_provider(factory, session_mfa_time=mfa_time); tid, _, _ = await _create_provider(factory)
            async with factory() as db:
                db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=mid, permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value, scope_type=TrustPermissionScope.GLOBAL.value, facility_id=None, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-MFA-BOUND")); await db.commit()
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mtoken}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("mfa-boundary")}, json={"target_provider_id": str(tid), "permission": "PROFESSIONAL_REVIEW"}); assert resp.status_code == expected_status
        await _test_session_mfa(now, 200); await _test_session_mfa(now - timedelta(minutes=14, seconds=59), 200); await _test_session_mfa(now - timedelta(minutes=15, seconds=1), 428)
    finally:
        await engine.dispose()


async def test_audit_chain_and_partitions():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, mgr_token, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory); fac_id = await _create_facility(factory)
        async with factory() as db:
            grant_res = await ProviderTrustRootGovernanceService(db).grant_root(operator_actor_id="secops-1", approver_actor_id="secops-2", target_provider_id=mgr_id, valid_until=now + timedelta(days=30), expected_active_root_count=0, governance_reference="AUDIT-CHAIN-ROOT", idempotency_key=_key("audit-root-g"), now=now); assert grant_res.grant_id is not None
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("audit-sub-glob")}, json={"target_provider_id": str(target_id), "permission": "PROFESSIONAL_REVIEW"})).status_code == 200
            assert (await client.post("/api/v2/provider-trust/permissions/grant", headers={"Authorization": f"Bearer {mgr_token}", "User-Agent": _USER_AGENT, "Idempotency-Key": _key("audit-sub-fac")}, json={"target_provider_id": str(target_id), "permission": "FACILITY_REVIEW", "facility_id": str(fac_id)})).status_code == 200
        async with factory() as db:
            rows = (await db.execute(text("SELECT event_type, chain_partition, payload FROM public.audit_outbox ORDER BY created_at"))).fetchall()
            root_events = [r for r in rows if r.event_type == "PROVIDER_TRUST_PERMISSION_GRANTED" and r.payload.get("metadata", {}).get("governance_mode") == "OFFLINE_ROOT"]
            assert len(root_events) == 1 and root_events[0].chain_partition == "platform:platform"
            glob_sub = [r for r in rows if r.event_type == "PROVIDER_TRUST_PERMISSION_GRANTED" and r.payload.get("metadata", {}).get("permission") == "PROFESSIONAL_REVIEW"]
            assert len(glob_sub) == 1 and glob_sub[0].chain_partition == "platform:platform"
            fac_sub = [r for r in rows if r.event_type == "PROVIDER_TRUST_PERMISSION_GRANTED" and r.payload.get("metadata", {}).get("permission") == "FACILITY_REVIEW"]
            assert len(fac_sub) == 1 and fac_sub[0].chain_partition == f"hospital:{fac_id}:platform"
            assert await db.scalar(text("SELECT count(*) FROM public.audit_outbox WHERE event_type IN ('PROVIDER_TRUST_ROOT_GRANTED', 'PROVIDER_TRUST_ROOT_REVOKED')")) == 0
    finally:
        await engine.dispose()


async def test_atomicity_qualification():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); mgr_id, _, _ = await _create_provider(factory); target_id, _, _ = await _create_provider(factory); valid_until = now + timedelta(days=30)
        async with factory() as db:
            with patch("app.services.provider_trust_root_governance.enqueue_audit_event", AsyncMock(side_effect=RuntimeError("simulated outbox failure"))):
                with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                    await ProviderTrustRootGovernanceService(db).grant_root(operator_actor_id="op1", approver_actor_id="appr1", target_provider_id=target_id, valid_until=valid_until, expected_active_root_count=0, governance_reference="ATOM-FAIL", idempotency_key=_key("atom-fail-1"), now=now)
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"), {"p": target_id}) == 0
        async with factory() as db:
            await ProviderTrustRootGovernanceService(db).grant_root(operator_actor_id="op1", approver_actor_id="appr1", target_provider_id=mgr_id, valid_until=valid_until, expected_active_root_count=0, governance_reference="ATOM-MGR", idempotency_key=_key("atom-mgr"), now=now)
        auth_mgr = TrustManagementAuthentication(provider_id=mgr_id, method=ClinicalAuthenticationMethod.PROVIDER_SESSION, session_authenticated=True, mfa_verified_at=now)
        async with factory() as db:
            with patch("app.services.provider_trust_permission_application.enqueue_audit_event", AsyncMock(side_effect=RuntimeError("simulated 4C outbox failure"))):
                with pytest.raises(ProviderTrustPermissionApplicationError) as exc_sub:
                    await ProviderTrustPermissionApplicationService(db).apply_grant(actor_id=mgr_id, authentication=auth_mgr, target_provider_id=target_id, permission=TrustManagementPermission.PROFESSIONAL_REVIEW, idempotency_key=_key("atom-4c-fail"), now=now)
                assert exc_sub.value.code == "TRANSACTION_INTEGRITY_FAILURE"
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"), {"p": target_id}) == 0
    finally:
        await engine.dispose()


async def test_clinical_separation_matrix():
    db_url = _get_db_url(); engine = create_async_engine(db_url); factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc); prov_id, _, _ = await _create_provider(factory); fac_id = await _create_facility(factory)
        async with factory() as db:
            for perm, scope, fac in ((TrustManagementPermission.TRUST_PERMISSION_MANAGE, TrustPermissionScope.GLOBAL, None), (TrustManagementPermission.PROFESSIONAL_REVIEW, TrustPermissionScope.GLOBAL, None), (TrustManagementPermission.FACILITY_REVIEW, TrustPermissionScope.FACILITY, fac_id), (TrustManagementPermission.AFFILIATION_MANAGE, TrustPermissionScope.FACILITY, fac_id)):
                db.add(ProviderTrustPermissionGrant(id=uuid.uuid4(), provider_id=prov_id, permission=perm.value, scope_type=scope.value, facility_id=fac, granted_at=now, valid_from=now, valid_until=now + timedelta(days=30), revoked_at=None, granted_by_actor_id="bootstrap", governance_reference="QUAL-CLINICAL-SEP"))
            await db.commit()
        async with factory() as db:
            assert await db.scalar(text("SELECT count(*) FROM public.provider_hospital_affiliation WHERE provider_id = :p"), {"p": prov_id}) == 0
            assert await db.scalar(text("SELECT count(*) FROM public.professional_verification WHERE provider_id = :p"), {"p": prov_id}) == 0
            prov_identity = await db.get(ProviderIdentity, prov_id); assert prov_identity is not None
            clin_svc = ClinicalEligibilityService(); auth = InteractiveClinicalAuthentication(provider_id=prov_id, hospital_id=fac_id, method=ClinicalAuthenticationMethod.PROVIDER_SESSION, session_authenticated=True, mfa_verified_at=now)
            for cap in (ClinicalCapability.DOCUMENTS_REVIEW, ClinicalCapability.DOCUMENTS_COMMIT, ClinicalCapability.RECORD_READ):
                assert not (await clin_svc.evaluate_interactive(db, prov_identity, auth, cap, now=now)).allowed
    finally:
        await engine.dispose()


async def test_route_surface_freeze():
    routes = [r for r in main_app.routes if getattr(r, "path", "").startswith("/api/v2/provider-trust")]
    assert len(routes) == 26
    assert len([r for r in routes if "POST" in getattr(r, "methods", set())]) == 26
    assert len([r for r in routes if "POST" not in getattr(r, "methods", set())]) == 0
    for r in routes:
        path = getattr(r, "path", "")
        assert "root" not in path.lower()
        assert "bootstrap" not in path.lower()
        assert "search" not in path.lower()
        assert "status" not in path.lower()


async def test_architecture_static_guards():
    app_dir = Path("app")
    for py in app_dir.rglob("*.py"):
        content = py.read_text(encoding="utf-8")
        for pat in ["allow_root", "bootstrap_root", "super_admin", "skip_authorization", "first_user_admin", "legacy_role_to_root", "root_via_http"]:
            assert pat not in content, f"Prohibited pattern {pat!r} found in {py}"
    import app.api.v2.provider_trust_permission_routes as p_routes
    assert not hasattr(p_routes, "ProviderTrustRootGovernanceService")
    import app.services.provider_trust_root_governance as p_gov
    import scripts.governance_trust_root as gov_cli
    for mod in (p_gov, gov_cli):
        content = Path(mod.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("fastapi", "starlette", "aioredis", "clinicaleligibilityservice"):
            assert forbidden not in content, f"Forbidden import {forbidden!r} in {mod.__file__}"
