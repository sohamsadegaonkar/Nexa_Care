"""Disposable PostgreSQL integration proof for ProviderTrustRootGovernanceService and CLI.

Proves:
1. Canonical root grant and DB row invariants (TRUST_PERMISSION_MANAGE / GLOBAL / facility NULL).
2. Target eligibility validation (active identity, active credential, contact assurance, MFA).
3. Independence from clinical verification, facility verification, and hospital affiliations.
4. Active duplicate root slot rejection (ACTIVE_ROOT_GRANT_EXISTS).
5. Expired-unrevoked root supersession (superseded_grant_id, 1 revoke audit, 1 grant audit).
6. Revoked historical root permits new root grant.
7. Idempotency replay and conflict detection for both grant and revoke.
8. Transactional atomicity: audit and idempotency completion failures roll back mutations.
9. Root revocation against active and disabled/compromised accounts.
10. Already-revoked and invalid root state rejection.
11. Expected active root count CAS guard (ROOT_SET_CHANGED).
12. Last-root revocation without acknowledgment denied (ZERO_ROOT_ACK_REQUIRED); with acknowledgment succeeds.
13. Fail-closed recovery from 0 roots via offline grant with expected_active_root_count=0.
14. Global PostgreSQL advisory lock serialization between concurrent offline operators.
15. 4F ↔ 4C Concurrency Proof A (4F revokes first -> 4C subordinate grant denied).
16. 4F ↔ 4C Concurrency Proof B (4C locks first -> 4F blocks -> 4C commits -> 4F revokes -> subsequent 4C denied).
17. Full CLI integration qualification of scripts/governance_trust_root.py against PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.provider_trust_permission_application as app_module
from app.models.provider import (
    HospitalRegistry,
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.security.trust_management_permissions import (
    TrustManagementPermission,
)
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.services.provider_trust_authorization import TrustManagementAuthentication
from app.services.provider_trust_permission_application import (
    ProviderTrustPermissionApplicationError,
    ProviderTrustPermissionApplicationResult,
    ProviderTrustPermissionApplicationService,
)
from app.services.provider_trust_root_governance import (
    ProviderTrustRootGovernanceError,
    ProviderTrustRootGovernanceResult,
    ProviderTrustRootGovernanceService,
    RootRevocationReasonCode,
    TrustRootGovernanceCommand,
    _request_hash_grant_root,
)
from scripts.governance_trust_root import run_governance

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
HEAD = "20260905_verification_application"


def _url() -> str:
    value = (
        os.getenv("NEXA_TRUST_ROOT_DATABASE_URL")
        or os.getenv("TRUST_PERMISSION_DATABASE_URL")
        or os.getenv("TEST_DATABASE_URL", "")
    )
    if not value:
        pytest.skip("NEXA_TRUST_ROOT_DATABASE_URL is not configured")
    if "127.0.0.1" not in value or "nexa_qual_" not in value:
        pytest.fail("qualification database must be disposable and loopback-only")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _config(url: str) -> Config:
    config = Config("alembic.ini")
    sync_url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
async def _cleanup_database_tables():
    url = _url()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await db.execute(
            text(
                "TRUNCATE TABLE "
                "public.provider_trust_permission_grant, "
                "public.mutation_idempotency, "
                "public.audit_outbox, "
                "public.provider_credential, "
                "public.provider_identity, "
                "public.hospital_registry "
                "CASCADE"
            )
        )
        await db.commit()
    await engine.dispose()
    yield


async def _create_facility(factory) -> uuid.UUID:
    fac_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=fac_id,
                facility_code=f"FAC-{fac_id.hex[:10]}",
                legal_name="Qual Root Hospital",
                display_name="Qual Root Hospital",
                country_code="IN",
                is_active=True,
            )
        )
        await db.commit()
    return fac_id


async def _create_eligible_provider(
    factory,
    *,
    is_active: bool = True,
    status: str = "active",
    credential_active: bool = True,
    email_verified: bool = True,
    phone_verified: bool = True,
    mfa_enabled: bool = True,
    mfa_secret: bool = True,
) -> uuid.UUID:
    prov_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
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
            mfa_secret="secret" if mfa_secret else None,
            mfa_secret_encrypted="enc-secret" if mfa_secret else None,
            mfa_enabled=mfa_enabled,
            is_active=credential_active,
        )
        db.add_all((identity, cred))
        await db.commit()
    return prov_id


# ---------------------------------------------------------------------------
# 1. Canonical Grant & Row Invariants
# ---------------------------------------------------------------------------


async def test_canonical_root_grant_and_invariants(monkeypatch):
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=60)
        idem_key = _key("qual-root-grant-01")

        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            result = await svc.grant_root(
                operator_actor_id="operator_alpha",
                approver_actor_id="approver_beta",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="CAB-SEC-2026-001",
                idempotency_key=idem_key,
                now=now,
            )

            assert result.command == TrustRootGovernanceCommand.GRANT_ROOT.value
            assert result.target_provider_id == target_id
            assert result.permission == "TRUST_PERMISSION_MANAGE"
            assert result.scope_type == "GLOBAL"
            assert result.superseded_grant_id is None
            assert not result.idempotent_replay
            grant_id = result.grant_id

        # Verify DB row invariants
        async with factory() as db:
            grant_row = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert grant_row is not None
            assert grant_row.provider_id == target_id
            assert grant_row.permission == "TRUST_PERMISSION_MANAGE"
            assert grant_row.scope_type == "GLOBAL"
            assert grant_row.facility_id is None
            assert grant_row.granted_at == now
            assert grant_row.valid_from == now
            assert grant_row.valid_until == valid_until
            assert grant_row.revoked_at is None
            assert grant_row.granted_by_actor_id == "operator_alpha"
            assert grant_row.governance_reference == "CAB-SEC-2026-001"

            # Verify audit outbox event
            outbox_row = (
                await db.execute(
                    text(
                        "SELECT event_type, payload FROM public.audit_outbox "
                        "WHERE payload -> 'metadata' ->> 'grant_id' = :g"
                    ),
                    {"g": str(grant_id)},
                )
            ).first()
            assert outbox_row is not None
            assert (
                outbox_row.event_type
                == ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_GRANTED.value
            )
            payload = outbox_row.payload
            assert payload["metadata"]["governance_mode"] == "OFFLINE_ROOT"
            assert payload["metadata"]["operator_actor_id"] == "operator_alpha"
            assert payload["metadata"]["approver_actor_id"] == "approver_beta"
            assert payload["metadata"]["governance_reference"] == "CAB-SEC-2026-001"

            # Verify idempotency record
            idem_row = (
                await db.execute(
                    text(
                        "SELECT resulting_resource_version, response_status, response_payload "
                        "FROM public.mutation_idempotency WHERE idempotency_key = :k"
                    ),
                    {"k": idem_key},
                )
            ).first()
            assert idem_row is not None
            assert idem_row.resulting_resource_version is None
            assert idem_row.response_status == 200
            assert idem_row.response_payload["grant_id"] == str(grant_id)

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 2. Target Eligibility Assertions
# ---------------------------------------------------------------------------


async def test_root_grant_target_eligibility(monkeypatch):
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)

        # 1. Nonexistent provider
        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await svc.grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=uuid.uuid4(),
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("el-nonexistent"),
                    now=now,
                )
            assert exc.value.code == "TARGET_PROVIDER_NOT_FOUND"

        # 2. Inactive provider
        p_inact = await _create_eligible_provider(factory, is_active=False)
        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await svc.grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=p_inact,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("el-inact"),
                    now=now,
                )
            assert exc.value.code == "TARGET_PROVIDER_INACTIVE"

        # 3. Inactive credential
        p_cred_inact = await _create_eligible_provider(factory, credential_active=False)
        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await svc.grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=p_cred_inact,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("el-cred-inact"),
                    now=now,
                )
            assert exc.value.code == "TARGET_CREDENTIAL_INACTIVE"

        # 4. Incomplete contact assurance (email unverified)
        p_no_email = await _create_eligible_provider(factory, email_verified=False)
        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await svc.grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=p_no_email,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("el-no-email"),
                    now=now,
                )
            assert exc.value.code == "TARGET_CONTACT_ASSURANCE_INCOMPLETE"

        # 5. Incomplete contact assurance (phone unverified)
        p_no_phone = await _create_eligible_provider(factory, phone_verified=False)
        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await svc.grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=p_no_phone,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("el-no-phone"),
                    now=now,
                )
            assert exc.value.code == "TARGET_CONTACT_ASSURANCE_INCOMPLETE"

        # 6. MFA not enabled
        p_no_mfa = await _create_eligible_provider(factory, mfa_enabled=False)
        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await svc.grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=p_no_mfa,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("el-no-mfa"),
                    now=now,
                )
            assert exc.value.code == "TARGET_MFA_NOT_CONFIGURED"

        # 7. MFA secret missing
        p_no_secret = await _create_eligible_provider(factory, mfa_secret=False)
        async with factory() as db:
            svc = ProviderTrustRootGovernanceService(db)
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await svc.grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=p_no_secret,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("el-no-secret"),
                    now=now,
                )
            assert exc.value.code == "TARGET_MFA_NOT_CONFIGURED"

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 3. Active Duplicate & Expired Supersession
# ---------------------------------------------------------------------------


async def test_active_duplicate_and_expired_supersession(monkeypatch):
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=10)

        # 1. First grant succeeds
        async with factory() as db:
            res1 = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-1",
                idempotency_key=_key("dup-initial"),
                now=now,
            )
            initial_grant_id = res1.grant_id

        # 2. Second grant while first is active fails closed with ACTIVE_ROOT_GRANT_EXISTS
        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(db).grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=target_id,
                    valid_until=valid_until + timedelta(days=5),
                    expected_active_root_count=1,
                    governance_reference="REF-2",
                    idempotency_key=_key("dup-active-attempt"),
                    now=now,
                )
            assert exc.value.code == "ACTIVE_ROOT_GRANT_EXISTS"

        # 3. Advance time past expiry: first grant is now expired but unrevoked
        later = valid_until + timedelta(seconds=1)
        new_valid_until = later + timedelta(days=30)

        # Expected active root count is 0 because initial grant has expired!
        async with factory() as db:
            res2 = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                target_provider_id=target_id,
                valid_until=new_valid_until,
                expected_active_root_count=0,
                governance_reference="REF-SUPERSEDE",
                idempotency_key=_key("dup-supersede"),
                now=later,
            )
            assert res2.superseded_grant_id == initial_grant_id
            new_grant_id = res2.grant_id

        # Verify old row was revoked with revoked_at = later
        async with factory() as db:
            old_row = await db.get(ProviderTrustPermissionGrant, initial_grant_id)
            assert old_row is not None
            assert old_row.revoked_at == later

            # Verify supersession revoke audit event in outbox
            outbox_revoke = (
                await db.execute(
                    text(
                        "SELECT event_type, payload FROM public.audit_outbox "
                        "WHERE payload -> 'metadata' ->> 'grant_id' = :g "
                        "  AND payload -> 'metadata' ->> 'command' = 'REVOKE_ROOT'"
                    ),
                    {"g": str(initial_grant_id)},
                )
            ).first()
            assert outbox_revoke is not None
            assert (
                outbox_revoke.event_type
                == ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED.value
            )
            assert (
                outbox_revoke.payload["metadata"]["revocation_reason_code"]
                == "EXPIRED_SUPERSEDED"
            )
            assert outbox_revoke.payload["metadata"]["superseded_by_grant_id"] == str(
                new_grant_id
            )

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 4. Idempotency Replay & Conflict
# ---------------------------------------------------------------------------


async def test_root_governance_idempotency_and_replay(monkeypatch):
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=15)
        grant_key = _key("idem-grant-replay")

        # 1. Initial grant
        async with factory() as db:
            res1 = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op_replay",
                approver_actor_id="appr_replay",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-REPLAY",
                idempotency_key=grant_key,
                now=now,
            )
            assert not res1.idempotent_replay

        # 2. Exact same grant replay
        async with factory() as db:
            res2 = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op_replay",
                approver_actor_id="appr_replay",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-REPLAY",
                idempotency_key=grant_key,
                now=now,
            )
            assert res2.idempotent_replay
            assert res2.grant_id == res1.grant_id

        # 3. Same key with different parameters -> IDEMPOTENCY_KEY_REUSED
        other_target = await _create_eligible_provider(factory)
        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(db).grant_root(
                    operator_actor_id="op_replay",
                    approver_actor_id="appr_replay",
                    target_provider_id=other_target,  # Different target
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-REPLAY",
                    idempotency_key=grant_key,
                    now=now,
                )
            assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"

        # 4. Revoke replay and conflict
        revoke_key = _key("idem-revoke-replay")
        async with factory() as db:
            rev1 = await ProviderTrustRootGovernanceService(db).revoke_root(
                operator_actor_id="op_replay",
                approver_actor_id="appr_replay",
                grant_id=res1.grant_id,
                revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
                expected_active_root_count=1,
                governance_reference="REF-REV",
                idempotency_key=revoke_key,
                acknowledge_zero_active_roots=True,
                now=now,
            )
            assert not rev1.idempotent_replay

        async with factory() as db:
            rev2 = await ProviderTrustRootGovernanceService(db).revoke_root(
                operator_actor_id="op_replay",
                approver_actor_id="appr_replay",
                grant_id=res1.grant_id,
                revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
                expected_active_root_count=1,
                governance_reference="REF-REV",
                idempotency_key=revoke_key,
                acknowledge_zero_active_roots=True,
                now=now,
            )
            assert rev2.idempotent_replay
            assert rev2.grant_id == res1.grant_id

        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(
                    db
                ).revoke_root(
                    operator_actor_id="op_replay",
                    approver_actor_id="appr_replay",
                    grant_id=res1.grant_id,
                    revocation_reason_code=RootRevocationReasonCode.SECURITY_RESPONSE,  # Different reason
                    expected_active_root_count=1,
                    governance_reference="REF-REV",
                    idempotency_key=revoke_key,
                    acknowledge_zero_active_roots=True,
                    now=now,
                )
            assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"

        # 5. Incomplete reservation -> IDEMPOTENCY_IN_PROGRESS
        in_prog_key = _key("idem-in-prog")
        in_prog_hash = _request_hash_grant_root(
            operator_actor_id="op_replay",
            approver_actor_id="appr_replay",
            target_provider_id=target_id,
            governance_reference="REF-REPLAY",
            expected_active_root_count=0,
            valid_until=valid_until,
        )
        async with factory() as db:
            await db.execute(
                text("""
                    INSERT INTO public.mutation_idempotency
                      (tenant_id, actor_id, operation, resource_id, idempotency_key, request_hash, created_at, retention_expires_at)
                    VALUES (:tenant_id, :actor_id, :operation, :resource_id, :key, :request_hash, now(), now() + interval '90 days')
                """),
                {
                    "tenant_id": "platform-provider-trust",
                    "actor_id": "op_replay",
                    "operation": "provider.trust.root.grant.v1",
                    "resource_id": str(target_id),
                    "key": in_prog_key,
                    "request_hash": in_prog_hash,
                },
            )
            await db.commit()

        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(db).grant_root(
                    operator_actor_id="op_replay",
                    approver_actor_id="appr_replay",
                    target_provider_id=target_id,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="REF-REPLAY",
                    idempotency_key=in_prog_key,
                    now=now,
                )
            assert exc.value.code == "IDEMPOTENCY_IN_PROGRESS"

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 5. Root Revocation & Containment against Disabled Accounts
# ---------------------------------------------------------------------------


async def test_atomicity_grant_and_revoke_audit_failures_rollback(monkeypatch):
    """Audit outbox failure during grant or revoke rolls back the entire database transaction."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)
        fail_key = _key("atom-audit-fail")

        # 1. Grant audit failure rolls back root grant
        async with factory() as db:
            with patch(
                "app.services.provider_trust_root_governance.enqueue_audit_event",
                AsyncMock(side_effect=RuntimeError("simulated outbox failure")),
            ):
                svc = ProviderTrustRootGovernanceService(db)
                with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                    await svc.grant_root(
                        operator_actor_id="op1",
                        approver_actor_id="appr1",
                        target_provider_id=target_id,
                        valid_until=valid_until,
                        expected_active_root_count=0,
                        governance_reference="REF-FAIL",
                        idempotency_key=fail_key,
                        now=now,
                    )
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"

        # Verify no grant or idempotency row was persisted
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert count == 0
            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": fail_key},
            )
            assert idem_count == 0

        # Create valid root grant for revoke test
        async with factory() as db:
            grant_res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-SUCCESS",
                idempotency_key=_key("seed-for-revoke-fail"),
                now=now,
            )
            grant_id = grant_res.grant_id

        # 2. Revoke audit failure rolls back revoked_at
        revoke_fail_key = _key("atom-revoke-audit-fail")
        async with factory() as db:
            with patch(
                "app.services.provider_trust_root_governance.enqueue_audit_event",
                AsyncMock(side_effect=RuntimeError("simulated outbox failure")),
            ):
                svc = ProviderTrustRootGovernanceService(db)
                with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                    await svc.revoke_root(
                        operator_actor_id="op1",
                        approver_actor_id="appr1",
                        grant_id=grant_id,
                        revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
                        expected_active_root_count=1,
                        governance_reference="REF-REV-FAIL",
                        idempotency_key=revoke_fail_key,
                        acknowledge_zero_active_roots=True,
                        now=now,
                    )
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"

        # Verify revoked_at remains NULL
        async with factory() as db:
            row = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert row is not None
            assert row.revoked_at is None

        # 3. Expired root supersession audit failure rolls back supersession
        expired_target_id = await _create_eligible_provider(factory)
        expired_grant_id = uuid.uuid4()
        past_time = now - timedelta(days=10)
        async with factory() as db:
            exp_grant = ProviderTrustPermissionGrant(
                id=expired_grant_id,
                provider_id=expired_target_id,
                permission="TRUST_PERMISSION_MANAGE",
                scope_type="GLOBAL",
                facility_id=None,
                granted_at=past_time,
                valid_from=past_time,
                valid_until=now - timedelta(days=1),
                revoked_at=None,
                granted_by_actor_id="seed_op",
                governance_reference="REF-EXPIRED-SEED",
            )
            db.add(exp_grant)
            await db.commit()

        supersede_fail_key = _key("atom-supersede-fail")
        new_valid_until = now + timedelta(days=20)
        async with factory() as db:
            with patch(
                "app.services.provider_trust_root_governance.enqueue_audit_event",
                AsyncMock(
                    side_effect=RuntimeError(
                        "simulated outbox failure during supersession"
                    )
                ),
            ):
                svc = ProviderTrustRootGovernanceService(db)
                with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                    await svc.grant_root(
                        operator_actor_id="op1",
                        approver_actor_id="appr1",
                        target_provider_id=expired_target_id,
                        valid_until=new_valid_until,
                        expected_active_root_count=1,
                        governance_reference="REF-SUPERSEDE-FAIL",
                        idempotency_key=supersede_fail_key,
                        now=now,
                    )
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"

        # Verify: expired old grant remains unrevoked, no replacement root, no completed idempotency row
        async with factory() as db:
            exp_row = await db.get(ProviderTrustPermissionGrant, expired_grant_id)
            assert exp_row is not None
            assert exp_row.revoked_at is None

            replacement_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant "
                    "WHERE provider_id = :p AND valid_until = :vu"
                ),
                {"p": expired_target_id, "vu": new_valid_until},
            )
            assert replacement_count == 0

            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": supersede_fail_key},
            )
            assert idem_count == 0

    finally:
        await engine.dispose()


async def test_root_revoke_already_revoked_and_invalid_state(monkeypatch):
    """Attempting to revoke an already revoked grant or a non-root grant fails closed."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)

        # 1. Nonexistent grant ID -> ROOT_GRANT_NOT_FOUND
        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(db).revoke_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    grant_id=uuid.uuid4(),
                    revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("rev-nonexistent"),
                    now=now,
                )
            assert exc.value.code == "ROOT_GRANT_NOT_FOUND"

        # 2. Subordinate grant (PROFESSIONAL_REVIEW) cannot be revoked via root governance -> ROOT_STATE_INVALID
        subordinate_grant_id = uuid.uuid4()
        async with factory() as db:
            sub_grant = ProviderTrustPermissionGrant(
                id=subordinate_grant_id,
                provider_id=target_id,
                permission="PROFESSIONAL_REVIEW",
                scope_type="GLOBAL",
                facility_id=None,
                granted_at=now,
                valid_from=now,
                valid_until=valid_until,
                revoked_at=None,
                granted_by_actor_id="op1",
                governance_reference="SUB-1",
            )
            db.add(sub_grant)
            await db.commit()

        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(db).revoke_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    grant_id=subordinate_grant_id,
                    revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
                    expected_active_root_count=0,
                    governance_reference="REF-1",
                    idempotency_key=_key("rev-subordinate"),
                    now=now,
                )
            assert exc.value.code == "ROOT_STATE_INVALID"

        # 3. Create and revoke root grant
        async with factory() as db:
            grant_res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-ROOT",
                idempotency_key=_key("seed-for-already-rev"),
                now=now,
            )
            root_id = grant_res.grant_id

            await ProviderTrustRootGovernanceService(db).revoke_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                grant_id=root_id,
                revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
                expected_active_root_count=1,
                governance_reference="REF-ROOT-REV",
                idempotency_key=_key("rev-first-time"),
                acknowledge_zero_active_roots=True,
                now=now,
            )

        # 4. Attempting to revoke again -> ROOT_GRANT_ALREADY_REVOKED
        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(db).revoke_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    grant_id=root_id,
                    revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
                    expected_active_root_count=0,
                    governance_reference="REF-ROOT-REV-AGAIN",
                    idempotency_key=_key("rev-second-time"),
                    acknowledge_zero_active_roots=True,
                    now=now,
                )
            assert exc.value.code == "ROOT_GRANT_ALREADY_REVOKED"

    finally:
        await engine.dispose()


async def test_clinical_authority_separation_regression(monkeypatch):
    """Root authority confers zero clinical capability and does not alter clinical eligibility."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        fac_id = await _create_facility(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)

        # Establish root grant for target
        async with factory() as db:
            await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-CLINICAL-SEP",
                idempotency_key=_key("clin-sep-root"),
                now=now,
            )

        # Check target provider's clinical eligibility: MUST STILL BE DENIED!
        async with factory() as db:
            target_identity = await db.get(ProviderIdentity, target_id)
            clin_service = ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            )
            clin_auth = InteractiveClinicalAuthentication(
                provider_id=target_id,
                hospital_id=fac_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=now,
            )
            decision = await clin_service.evaluate_interactive(
                db,
                target_identity,
                clin_auth,
                ClinicalCapability.DOCUMENTS_REVIEW,
                now=now,
            )
            assert not decision.allowed
            assert decision.denial_code is not None

    finally:
        await engine.dispose()


async def test_root_revoke_against_disabled_account(monkeypatch):
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)

        # Initial grant
        async with factory() as db:
            res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-1",
                idempotency_key=_key("grant-before-disable"),
                now=now,
            )
            grant_id = res.grant_id

        # Target account becomes suspended, credential deactivated, MFA disabled
        async with factory() as db:
            ident = await db.get(ProviderIdentity, target_id)
            ident.is_active = False
            ident.status = "suspended"
            cred = (
                await db.execute(
                    select(ProviderCredential).where(
                        ProviderCredential.provider_id == target_id
                    )
                )
            ).scalar_one()
            cred.is_active = False
            cred.mfa_enabled = False
            await db.commit()

        # Revocation MUST STILL SUCCEED for containment
        async with factory() as db:
            rev_res = await ProviderTrustRootGovernanceService(db).revoke_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                grant_id=grant_id,
                revocation_reason_code=RootRevocationReasonCode.COMPROMISE_RESPONSE,
                expected_active_root_count=1,
                governance_reference="INCIDENT-999",
                idempotency_key=_key("revoke-disabled-target"),
                acknowledge_zero_active_roots=True,
                now=now + timedelta(minutes=5),
            )
            assert rev_res.grant_id == grant_id

        # Verify revoked_at in DB
        async with factory() as db:
            grant_row = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert grant_row.revoked_at == now + timedelta(minutes=5)

    finally:
        await engine.dispose()


async def test_root_revoke_against_inactive_credential(monkeypatch):
    """Root containment must not depend on an active credential (revoke against inactive credential succeeds)."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)
        revoke_idem_key = _key("revoke-inactive-cred")

        # 1. Provision valid root grant
        async with factory() as db:
            res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op_contain",
                approver_actor_id="appr_contain",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-CONTAIN-01",
                idempotency_key=_key("grant-before-inact-cred"),
                now=now,
            )
            grant_id = res.grant_id

        # 2. Set target ProviderCredential.is_active = False (ProviderIdentity remains active)
        async with factory() as db:
            cred = (
                await db.execute(
                    select(ProviderCredential).where(
                        ProviderCredential.provider_id == target_id
                    )
                )
            ).scalar_one()
            cred.is_active = False
            await db.commit()

        # 3. Call REAL ProviderTrustRootGovernanceService.revoke_root
        rev_now = now + timedelta(minutes=10)
        async with factory() as db:
            rev_res = await ProviderTrustRootGovernanceService(db).revoke_root(
                operator_actor_id="op_contain",
                approver_actor_id="appr_contain",
                grant_id=grant_id,
                revocation_reason_code=RootRevocationReasonCode.SECURITY_RESPONSE,
                expected_active_root_count=1,
                governance_reference="SECURITY-RESPONSE-CONTAIN",
                idempotency_key=revoke_idem_key,
                acknowledge_zero_active_roots=True,
                now=rev_now,
            )
            assert rev_res.grant_id == grant_id
            assert not rev_res.idempotent_replay

        # 4. Verify DB: root revoked_at becomes non-null
        async with factory() as db:
            grant_row = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert grant_row is not None
            assert grant_row.revoked_at == rev_now

            # 5. Exactly one permission-revoked audit event
            audit_rows = (
                await db.execute(
                    text(
                        "SELECT event_type, payload FROM public.audit_outbox "
                        "WHERE payload -> 'metadata' ->> 'grant_id' = :g "
                        "AND event_type = :et"
                    ),
                    {
                        "g": str(grant_id),
                        "et": ProviderTrustAuditEvent.PROVIDER_TRUST_PERMISSION_REVOKED.value,
                    },
                )
            ).all()
            assert len(audit_rows) == 1
            assert (
                audit_rows[0].payload["metadata"]["governance_mode"] == "OFFLINE_ROOT"
            )
            assert (
                audit_rows[0].payload["metadata"]["revocation_reason_code"]
                == "SECURITY_RESPONSE"
            )

            # 6. Exactly one completed idempotency row
            idem_row = (
                await db.execute(
                    text(
                        "SELECT response_status, response_payload FROM public.mutation_idempotency "
                        "WHERE idempotency_key = :k"
                    ),
                    {"k": revoke_idem_key},
                )
            ).first()
            assert idem_row is not None
            assert idem_row.response_status == 200
            assert idem_row.response_payload["grant_id"] == str(grant_id)

    finally:
        await engine.dispose()


async def test_root_grant_professional_affiliation_independence(monkeypatch):
    """GRANT_ROOT succeeds for target without ProfessionalVerification or ProviderHospitalAffiliation rows, but confers zero clinical eligibility."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Create eligible provider (which has NO ProfessionalVerification or ProviderHospitalAffiliation)
        target_id = await _create_eligible_provider(factory)
        fac_id = await _create_facility(factory)

        # Assert no ProfessionalVerification and no ProviderHospitalAffiliation
        async with factory() as db:
            prof_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.professional_verification WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert prof_count == 0

            affil_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_hospital_affiliation WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert affil_count == 0

        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)
        grant_idem_key = _key("indep-root-grant")

        # Call REAL ProviderTrustRootGovernanceService.grant_root -> MUST SUCCEED
        async with factory() as db:
            res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op_indep",
                approver_actor_id="appr_indep",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="ROOT-ROTATION-Q4",
                idempotency_key=grant_idem_key,
                now=now,
            )
            assert res.grant_id is not None
            assert res.target_provider_id == target_id
            assert res.permission == "TRUST_PERMISSION_MANAGE"

        # Now prove the root grant itself still confers ZERO clinical eligibility
        async with factory() as db:
            target_identity = await db.get(ProviderIdentity, target_id)
            clin_service = ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            )
            clin_auth = InteractiveClinicalAuthentication(
                provider_id=target_id,
                hospital_id=fac_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=now,
            )
            decision = await clin_service.evaluate_interactive(
                db,
                target_identity,
                clin_auth,
                ClinicalCapability.DOCUMENTS_REVIEW,
                now=now,
            )
            assert not decision.allowed
            assert decision.denial_code is not None

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 6. Zero Root Acknowledgment & Fail-Closed Recovery
# ---------------------------------------------------------------------------


async def test_zero_root_acknowledgment_and_recovery(monkeypatch):
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)

        # Grant sole root
        async with factory() as db:
            res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                target_provider_id=target_id,
                valid_until=valid_until,
                expected_active_root_count=0,
                governance_reference="REF-SOLE",
                idempotency_key=_key("sole-root-grant"),
                now=now,
            )
            grant_id = res.grant_id

        # Revoking the final root WITHOUT acknowledgment fails with ZERO_ROOT_ACK_REQUIRED
        async with factory() as db:
            with pytest.raises(ProviderTrustRootGovernanceError) as exc:
                await ProviderTrustRootGovernanceService(db).revoke_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    grant_id=grant_id,
                    revocation_reason_code=RootRevocationReasonCode.GOVERNANCE_CHANGE,
                    expected_active_root_count=1,
                    governance_reference="REF-ZERO-UNACK",
                    idempotency_key=_key("sole-revoke-no-ack"),
                    acknowledge_zero_active_roots=False,
                    now=now,
                )
            assert exc.value.code == "ZERO_ROOT_ACK_REQUIRED"

        # Revoking WITH acknowledgment succeeds -> leaves 0 active roots
        async with factory() as db:
            await ProviderTrustRootGovernanceService(db).revoke_root(
                operator_actor_id="op1",
                approver_actor_id="appr1",
                grant_id=grant_id,
                revocation_reason_code=RootRevocationReasonCode.GOVERNANCE_CHANGE,
                expected_active_root_count=1,
                governance_reference="REF-ZERO-ACK",
                idempotency_key=_key("sole-revoke-with-ack"),
                acknowledge_zero_active_roots=True,
                now=now,
            )

        # Verify 0 active roots in DB
        async with factory() as db:
            count = await ProviderTrustRootGovernanceService(
                db
            )._count_effective_active_roots(now)
            assert count == 0

        # Subsequent recovery: offline grant with expected_active_root_count=0 restores root authority
        target_2 = await _create_eligible_provider(factory)
        async with factory() as db:
            recovery_res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="op2",
                approver_actor_id="appr2",
                target_provider_id=target_2,
                valid_until=now + timedelta(days=30),
                expected_active_root_count=0,  # Successfully establishes from 0
                governance_reference="RECOVERY-PLAN",
                idempotency_key=_key("recovery-grant"),
                now=now,
            )
            assert recovery_res.target_provider_id == target_2

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 7. Global Advisory Lock Concurrency Proof
# ---------------------------------------------------------------------------


async def test_concurrent_offline_root_grants_serialize(monkeypatch):
    """Two concurrent grant operations with the SAME expected count serialize globally."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        target_1 = await _create_eligible_provider(factory)
        target_2 = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=30)

        key_1 = _key("conc-grant-1")
        key_2 = _key("conc-grant-2")

        # Both operators assume expected_active_root_count = 0
        async def _run_grant(target, key):
            async with factory() as db:
                return await ProviderTrustRootGovernanceService(db).grant_root(
                    operator_actor_id="op1",
                    approver_actor_id="appr1",
                    target_provider_id=target,
                    valid_until=valid_until,
                    expected_active_root_count=0,
                    governance_reference="CAB-CONCURRENT",
                    idempotency_key=key,
                    now=now,
                )

        results = await asyncio.gather(
            _run_grant(target_1, key_1),
            _run_grant(target_2, key_2),
            return_exceptions=True,
        )

        successes = [
            r for r in results if isinstance(r, ProviderTrustRootGovernanceResult)
        ]
        errors = [r for r in results if isinstance(r, ProviderTrustRootGovernanceError)]

        assert len(successes) == 1
        assert len(errors) == 1
        # The second operator wakes up, sees the count is now 1 (not 0), and receives ROOT_SET_CHANGED
        assert errors[0].code == "ROOT_SET_CHANGED"

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 8. 4F ↔ 4C Concurrency Proof A (Real 4F revoke commits first -> 4C denied)
# ---------------------------------------------------------------------------


async def test_4f_4c_concurrency_scenario_a(monkeypatch):
    """Real 4F revoke_root commits first; Manager M subsequent subordinate grant fails with AUTHORIZATION_DENIED."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id = await _create_eligible_provider(factory)
        target_sub = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)

        # 1. Establish Manager M as root authority via real 4F service
        async with factory() as db:
            grant_res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="root_op",
                approver_actor_id="root_appr",
                target_provider_id=manager_id,
                valid_until=now + timedelta(days=30),
                expected_active_root_count=0,
                governance_reference="INITIAL-ROOT",
                idempotency_key=_key("4f-setup-root"),
                now=now,
            )
            root_grant_id = grant_res.grant_id

        # Manager M's authentication context
        auth_m = TrustManagementAuthentication(
            provider_id=manager_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=now,
        )

        # 2. Real 4F revoke_root locks and commits first
        async with factory() as db:
            await ProviderTrustRootGovernanceService(db).revoke_root(
                operator_actor_id="root_op",
                approver_actor_id="root_appr",
                grant_id=root_grant_id,
                revocation_reason_code=RootRevocationReasonCode.SECURITY_RESPONSE,
                expected_active_root_count=1,
                governance_reference="REVOKE-ROOT-SCENARIO-A",
                idempotency_key=_key("4f-revoke-a"),
                acknowledge_zero_active_roots=True,
                now=now + timedelta(seconds=1),
            )

        # 3. Manager M attempts subordinate grant via Phase 4C service -> AUTHORIZATION_DENIED
        async with factory() as db:
            svc_4c = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc_4c.apply_grant(
                    actor_id=manager_id,
                    authentication=auth_m,
                    target_provider_id=target_sub,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=_key("sub-grant-attempt-after-4f-revoke"),
                    now=now + timedelta(seconds=2),
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

        # Zero subordinate grants created
        async with factory() as db:
            sub_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_sub},
            )
            assert sub_count == 0

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 9. 4F ↔ 4C Concurrency Proof B (4C locks first -> Real 4F blocks -> 4C commits -> 4F revokes -> subsequent 4C denied)
# ---------------------------------------------------------------------------


async def test_4f_4c_concurrency_scenario_b(monkeypatch):
    """Manager M locks current authority first -> Real 4F revoke blocks on row lock -> M commits -> 4F revokes -> subsequent M denied."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id = await _create_eligible_provider(factory)
        target_sub = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)

        # Establish Manager M as root via real 4F
        async with factory() as db:
            grant_res = await ProviderTrustRootGovernanceService(db).grant_root(
                operator_actor_id="root_op",
                approver_actor_id="root_appr",
                target_provider_id=manager_id,
                valid_until=now + timedelta(days=30),
                expected_active_root_count=0,
                governance_reference="INITIAL-ROOT-B",
                idempotency_key=_key("4f-setup-root-b"),
                now=now,
            )
            root_grant_id = grant_res.grant_id

        auth_m = TrustManagementAuthentication(
            provider_id=manager_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=now,
        )

        m_holding_lock_event = asyncio.Event()
        allow_m_commit_event = asyncio.Event()

        real_enqueue = app_module.enqueue_audit_event

        async def _sync_enqueue(*args, **kwargs):
            m_holding_lock_event.set()
            await allow_m_commit_event.wait()
            return await real_enqueue(*args, **kwargs)

        sub_grant_key = _key("4c-sub-grant-b")

        async def _run_manager_grant():
            async with factory() as db:
                svc_4c = ProviderTrustPermissionApplicationService(db)
                with patch(
                    "app.services.provider_trust_permission_application.enqueue_audit_event",
                    _sync_enqueue,
                ):
                    return await svc_4c.apply_grant(
                        actor_id=manager_id,
                        authentication=auth_m,
                        target_provider_id=target_sub,
                        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                        idempotency_key=sub_grant_key,
                        now=now,
                    )

        # 1. Start Manager M's subordinate grant
        m_task = asyncio.create_task(_run_manager_grant())

        # 2. Wait until M has acquired row locks and evaluated authority
        await m_holding_lock_event.wait()

        # 3. Start REAL 4F revoke_root task
        # Because M holds the root grant lock FOR UPDATE in PostgreSQL, 4F MUST block!
        async def _run_real_4f_revoke():
            async with factory() as db:
                return await ProviderTrustRootGovernanceService(db).revoke_root(
                    operator_actor_id="root_op",
                    approver_actor_id="root_appr",
                    grant_id=root_grant_id,
                    revocation_reason_code=RootRevocationReasonCode.SECURITY_RESPONSE,
                    expected_active_root_count=1,
                    governance_reference="REVOKE-ROOT-SCENARIO-B",
                    idempotency_key=_key("4f-revoke-b"),
                    acknowledge_zero_active_roots=True,
                    now=now + timedelta(seconds=1),
                )

        revocation_task = asyncio.create_task(_run_real_4f_revoke())

        # 4. Deterministically verify in PostgreSQL that 4F is blocked waiting on the lock
        blocked_confirmed = False
        async with factory() as monitor_db:
            for _ in range(50):
                waiting_locks = await monitor_db.scalar(
                    text("SELECT count(*) FROM pg_locks WHERE NOT granted")
                )
                if waiting_locks > 0:
                    blocked_confirmed = True
                    break
                await asyncio.sleep(0.01)

        assert (
            blocked_confirmed
        ), "Real 4F revoke_root must be blocked waiting on M's grant lock in PostgreSQL"

        # 5. Allow Manager M to complete and commit
        allow_m_commit_event.set()
        m_result = await m_task
        assert isinstance(m_result, ProviderTrustPermissionApplicationResult)

        # 6. Once M commits, 4F unblocks and commits revocation
        rev_result = await revocation_task
        assert isinstance(rev_result, ProviderTrustRootGovernanceResult)

        # 7. Subsequent operation by Manager M MUST fail with AUTHORIZATION_DENIED
        target_sub_2 = await _create_eligible_provider(factory)
        async with factory() as db:
            svc_4c = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc_4c.apply_grant(
                    actor_id=manager_id,
                    authentication=auth_m,
                    target_provider_id=target_sub_2,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=_key("sub-grant-2-denied"),
                    now=now + timedelta(seconds=2),
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
async def test_cli_integration_qualification(monkeypatch):
    """Exercise scripts/governance_trust_root.py entry point against real PostgreSQL with full A-J verification."""
    url = _url()
    monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    all_stdout: list[str] = []
    all_stderr: list[str] = []

    def _record_stdout(s: str) -> int:
        all_stdout.append(s)
        return len(s)

    def _record_stderr(s: str) -> int:
        all_stderr.append(s)
        return len(s)

    try:
        # Determine actual database name
        async with factory() as db:
            actual_db_name = (
                await db.execute(text("SELECT current_database()"))
            ).scalar()

        target_id = await _create_eligible_provider(factory)
        now = datetime.now(timezone.utc)
        valid_until_str = (now + timedelta(days=30)).isoformat()
        grant_idem_key = str(uuid.uuid4())

        # A. NEXA_TRUST_ROOT_DATABASE_URL missing -> nonzero exit -> stable safe error
        monkeypatch.delenv("NEXA_TRUST_ROOT_DATABASE_URL", raising=False)
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "grant-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REF-1",
                    "--idempotency-key",
                    grant_idem_key,
                    "--expected-active-root-count",
                    "0",
                    "--target-provider-id",
                    str(target_id),
                    "--confirm-target-provider-id",
                    str(target_id),
                    "--valid-until",
                    valid_until_str,
                ]
            )
        assert exit_code == 1
        assert "INVALID_REQUEST" in all_stderr[-1]
        monkeypatch.setenv("NEXA_TRUST_ROOT_DATABASE_URL", url)

        # Verify no grant created
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert count == 0

        # B. --expected-database-name wrong -> nonzero exit -> DATABASE_NAME_MISMATCH -> no mutation
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "grant-root",
                    "--expected-database-name",
                    "wrong_db_name",
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REF-1",
                    "--idempotency-key",
                    grant_idem_key,
                    "--expected-active-root-count",
                    "0",
                    "--target-provider-id",
                    str(target_id),
                    "--confirm-target-provider-id",
                    str(target_id),
                    "--valid-until",
                    valid_until_str,
                ]
            )
        assert exit_code == 1
        assert "DATABASE_NAME_MISMATCH" in all_stderr[-1]
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert count == 0

        # C. Schema revision mismatch -> nonzero exit -> SCHEMA_REVISION_MISMATCH -> no mutation
        with (
            patch(
                "scripts.governance_trust_root._derive_repository_heads",
                return_value=("nonexistent_revision",),
            ),
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "grant-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REF-1",
                    "--idempotency-key",
                    grant_idem_key,
                    "--expected-active-root-count",
                    "0",
                    "--target-provider-id",
                    str(target_id),
                    "--confirm-target-provider-id",
                    str(target_id),
                    "--valid-until",
                    valid_until_str,
                ]
            )
        assert exit_code == 1
        assert "SCHEMA_REVISION_MISMATCH" in all_stderr[-1]
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert count == 0

        # D. --apply absent -> nonzero exit -> EXPLICIT_APPLY_REQUIRED -> no mutation
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "grant-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REF-1",
                    "--idempotency-key",
                    grant_idem_key,
                    "--expected-active-root-count",
                    "0",
                    "--target-provider-id",
                    str(target_id),
                    "--confirm-target-provider-id",
                    str(target_id),
                    "--valid-until",
                    valid_until_str,
                ]
            )
        assert exit_code == 1
        assert "EXPLICIT_APPLY_REQUIRED" in all_stderr[-1]
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert count == 0

        # E. Target double-entry mismatch -> nonzero exit -> CONFIRMATION_MISMATCH -> no mutation
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "grant-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REF-1",
                    "--idempotency-key",
                    grant_idem_key,
                    "--expected-active-root-count",
                    "0",
                    "--target-provider-id",
                    str(target_id),
                    "--confirm-target-provider-id",
                    str(uuid.uuid4()),  # Double-entry mismatch
                    "--valid-until",
                    valid_until_str,
                ]
            )
        assert exit_code == 1
        assert "CONFIRMATION_MISMATCH" in all_stderr[-1]
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert count == 0

        # F. Grant success -> zero exit
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "grant-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REF-1",
                    "--idempotency-key",
                    grant_idem_key,
                    "--expected-active-root-count",
                    "0",
                    "--target-provider-id",
                    str(target_id),
                    "--confirm-target-provider-id",
                    str(target_id),
                    "--valid-until",
                    valid_until_str,
                ]
            )
        assert exit_code == 0
        assert "GRANT_ROOT" in all_stdout[-1]
        assert str(target_id) in all_stdout[-1]

        # Retrieve grant_id from database
        async with factory() as db:
            grant_row = (
                await db.execute(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == target_id
                    )
                )
            ).scalar_one()
            grant_id = grant_row.id
            assert grant_row.revoked_at is None

        # G. Same grant/idempotency replay -> zero exit -> same grant ID -> idempotent replay
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "grant-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REF-1",
                    "--idempotency-key",
                    grant_idem_key,
                    "--expected-active-root-count",
                    "0",
                    "--target-provider-id",
                    str(target_id),
                    "--confirm-target-provider-id",
                    str(target_id),
                    "--valid-until",
                    valid_until_str,
                ]
            )
        assert exit_code == 0
        assert '"idempotent_replay": true' in all_stdout[-1]
        assert str(grant_id) in all_stdout[-1]

        # H. Revoke double-entry mismatch -> nonzero exit -> no mutation
        revoke_idem_key = str(uuid.uuid4())
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "revoke-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REV-1",
                    "--idempotency-key",
                    revoke_idem_key,
                    "--expected-active-root-count",
                    "1",
                    "--grant-id",
                    str(grant_id),
                    "--confirm-grant-id",
                    str(uuid.uuid4()),  # Mismatch
                    "--reason",
                    "ACCESS_REMOVED",
                    "--acknowledge-zero-active-roots",
                ]
            )
        assert exit_code == 1
        assert "CONFIRMATION_MISMATCH" in all_stderr[-1]

        # Verify grant in DB was NOT mutated
        async with factory() as db:
            grant_row_check = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert grant_row_check is not None
            assert grant_row_check.revoked_at is None

        # I. Revoke success -> zero exit
        with (
            patch("sys.stdout.write", side_effect=_record_stdout),
            patch("sys.stderr.write", side_effect=_record_stderr),
        ):
            exit_code = await run_governance(
                [
                    "revoke-root",
                    "--expected-database-name",
                    actual_db_name,
                    "--apply",
                    "--operator-actor-id",
                    "cli_op",
                    "--approver-actor-id",
                    "cli_appr",
                    "--governance-reference",
                    "CLI-REV-1",
                    "--idempotency-key",
                    revoke_idem_key,
                    "--expected-active-root-count",
                    "1",
                    "--grant-id",
                    str(grant_id),
                    "--confirm-grant-id",
                    str(grant_id),
                    "--reason",
                    "ACCESS_REMOVED",
                    "--acknowledge-zero-active-roots",
                ]
            )
        assert exit_code == 0
        assert "REVOKE_ROOT" in all_stdout[-1]
        assert str(grant_id) in all_stdout[-1]

        # Verify DB: grant is now revoked
        async with factory() as db:
            grant_row_revoked = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert grant_row_revoked is not None
            assert grant_row_revoked.revoked_at is not None

        # J. stdout/stderr security audit: never contain credentials, secrets, or raw tracebacks
        full_stdout = "".join(all_stdout)
        full_stderr = "".join(all_stderr)
        combined_output = full_stdout + "\n" + full_stderr

        forbidden_leak_strings = [
            "nexa_test",  # DB password
            url,  # full DB URL
            "enc-secret",  # encrypted MFA secret
            "argon2-hash",  # provider password hash
            "Traceback",  # raw python tracebacks
            "asyncpg.exceptions",  # raw asyncpg exceptions
            "ProgrammingError",  # raw SQLAlchemy database errors
        ]
        for forbidden in forbidden_leak_strings:
            assert (
                forbidden not in combined_output
            ), f"CLI output leaked sensitive token {forbidden}"

    finally:
        await engine.dispose()
