"""Disposable PostgreSQL integration proof for ProviderTrustPermissionApplicationService.

Covers canonical grant/revoke mutations, duplicate slot rejection, expired supersession,
deterministic lock ordering, deadlock-free reciprocal manager concurrency, duplicate grant race,
revoke race, idempotency race, both root revocation race linearizations (Scenario A and Scenario B),
atomicity rollbacks for grant/revoke/supersession/idempotency/authorization/policy, and strict clinical
authority separation.
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
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
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
from app.services.provider_trust_authorization import (
    TrustManagementAuthentication,
)
from app.services.provider_trust_permission_application import (
    ProviderTrustPermissionApplicationError,
    ProviderTrustPermissionApplicationResult,
    ProviderTrustPermissionApplicationService,
)
from app.services.provider_trust_permission_policy import RevocationReasonCode

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
HEAD = "20260905_verification_application"


def _url() -> str:
    value = os.getenv("TRUST_PERMISSION_DATABASE_URL") or os.getenv(
        "TRUST_LIFECYCLE_DATABASE_URL", ""
    )
    if not value:
        pytest.skip("TRUST_PERMISSION_DATABASE_URL is not configured")
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


async def _create_facility(factory) -> uuid.UUID:
    fac_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=fac_id,
                facility_code=f"FAC-{fac_id.hex[:10]}",
                legal_name="Qual Hospital",
                display_name="Qual Hospital",
                country_code="IN",
                is_active=True,
            )
        )
        await db.commit()
    return fac_id


async def _create_provider(
    factory,
    *,
    is_active: bool = True,
    status: str = "active",
    credential_active: bool = True,
) -> uuid.UUID:
    prov_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
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
            mfa_secret="secret",
            mfa_enabled=True,
            is_active=credential_active,
        )
        db.add_all((identity, cred))
        await db.commit()
    return prov_id


async def _create_root_manager(
    factory,
) -> tuple[uuid.UUID, TrustManagementAuthentication]:
    prov_id = await _create_provider(factory)
    now = datetime.now(timezone.utc)
    async with factory() as db:
        root_grant = ProviderTrustPermissionGrant(
            id=uuid.uuid4(),
            provider_id=prov_id,
            permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
            scope_type=TrustPermissionScope.GLOBAL.value,
            facility_id=None,
            granted_at=now - timedelta(days=1),
            valid_from=now - timedelta(days=1),
            valid_until=None,
            revoked_at=None,
            granted_by_actor_id="root-offline-authority",
            governance_reference="QUAL-ROOT",
        )
        db.add(root_grant)
        await db.commit()

    auth = TrustManagementAuthentication(
        provider_id=prov_id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=now,
    )
    return prov_id, auth


async def _offline_governance_revoke_root(
    factory,
    provider_id: uuid.UUID,
    revoked_at: datetime,
) -> None:
    """Simulate future-offline-governance root authority revocation using compatible lock discipline.

    NOTE: This test seam is future-offline-governance simulation only, NOT public root administration.
    Acquires sorted locks on ProviderIdentity, ProviderCredential, and ProviderTrustPermissionGrant
    with FOR UPDATE before mutating revoked_at.
    """
    async with factory() as db:
        async with db.begin():
            # Compatible lock discipline: lock identity, credential, and grant FOR UPDATE
            await db.execute(
                select(ProviderIdentity)
                .where(ProviderIdentity.id == provider_id)
                .with_for_update()
            )
            await db.execute(
                select(ProviderCredential)
                .where(ProviderCredential.provider_id == provider_id)
                .with_for_update()
            )
            root_grant = (
                await db.execute(
                    select(ProviderTrustPermissionGrant)
                    .where(
                        ProviderTrustPermissionGrant.provider_id == provider_id,
                        ProviderTrustPermissionGrant.permission
                        == TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                        ProviderTrustPermissionGrant.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if root_grant is not None:
                root_grant.revoked_at = revoked_at


async def test_ordinary_subordinate_grants_succeed_and_audit(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        fac_id = await _create_facility(factory)
        now = datetime.now(timezone.utc)

        k1 = _key("qual-grant-prof")
        k2 = _key("qual-grant-fac")
        k3 = _key("qual-grant-affil")

        # 1. PROFESSIONAL_REVIEW (GLOBAL)
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res1 = await svc.apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=k1,
                now=now,
            )
            assert res1.permission == "PROFESSIONAL_REVIEW"
            assert res1.scope_type == "GLOBAL"
            assert res1.facility_id is None
            assert not res1.idempotent_replay

        # 2. FACILITY_REVIEW (FACILITY)
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res2 = await svc.apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.FACILITY_REVIEW,
                facility_id=fac_id,
                idempotency_key=k2,
                now=now,
            )
            assert res2.permission == "FACILITY_REVIEW"
            assert res2.scope_type == "FACILITY"
            assert res2.facility_id == fac_id

        # 3. AFFILIATION_MANAGE (FACILITY)
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res3 = await svc.apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.AFFILIATION_MANAGE,
                facility_id=fac_id,
                idempotency_key=k3,
                now=now,
            )
            assert res3.permission == "AFFILIATION_MANAGE"
            assert res3.scope_type == "FACILITY"
            assert res3.facility_id == fac_id

        # Verify grants and audit outbox in database
        async with factory() as db:
            grants = (
                await db.scalars(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == target_id
                    )
                )
            ).all()
            assert len(grants) == 3

            outbox_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.audit_outbox WHERE actor_id = :actor_id"
                ),
                {"actor_id": str(manager_id)},
            )
            assert outbox_count == 3

    finally:
        await engine.dispose()


async def test_root_grant_and_revoke_denied_offline_only(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        # Grant root permission -> denied
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=manager_id,
                    authentication=auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.TRUST_PERMISSION_MANAGE,
                    idempotency_key=_key("qual-root-grant-deny"),
                    now=now,
                )
            assert exc.value.code == "TRUST_PERMISSION_POLICY_DENIED"
            assert exc.value.policy_code == "ROOT_PERMISSION_OFFLINE_ONLY"

        # Revoke root permission -> denied
        async with factory() as db:
            root_grant_id = await db.scalar(
                select(ProviderTrustPermissionGrant.id).where(
                    ProviderTrustPermissionGrant.provider_id == manager_id
                )
            )
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_revoke(
                    actor_id=manager_id,
                    authentication=auth,
                    grant_id=root_grant_id,
                    revocation_reason_code=RevocationReasonCode.SECURITY_RESPONSE,
                    idempotency_key=_key("qual-root-revoke-deny"),
                    now=now,
                )
            assert exc.value.code == "TRUST_PERMISSION_POLICY_DENIED"
            assert exc.value.policy_code == "ROOT_PERMISSION_OFFLINE_ONLY"

    finally:
        await engine.dispose()


async def test_self_grant_denied_and_subordinate_self_revoke_succeeds(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        now = datetime.now(timezone.utc)

        # Self grant -> denied
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=manager_id,
                    authentication=auth,
                    target_provider_id=manager_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=_key("qual-self-grant-deny"),
                    now=now,
                )
            assert exc.value.code == "TRUST_PERMISSION_POLICY_DENIED"
            assert exc.value.policy_code == "SELF_GRANT_PROHIBITED"

        # Subordinate self revoke: give manager a subordinate grant first via direct test setup
        sub_grant_id = uuid.uuid4()
        async with factory() as db:
            db.add(
                ProviderTrustPermissionGrant(
                    id=sub_grant_id,
                    provider_id=manager_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,
                    scope_type=TrustPermissionScope.GLOBAL.value,
                    facility_id=None,
                    granted_at=now,
                    valid_from=now,
                    valid_until=None,
                    revoked_at=None,
                    granted_by_actor_id="test-offline",
                )
            )
            await db.commit()

        # Manager revoking own subordinate grant -> succeeds!
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res = await svc.apply_revoke(
                actor_id=manager_id,
                authentication=auth,
                grant_id=sub_grant_id,
                revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
                idempotency_key=_key("qual-self-revoke-ok"),
                now=now,
            )
            assert res.command == "REVOKE"
            assert res.grant_id == sub_grant_id
            assert res.target_provider_id == manager_id

        async with factory() as db:
            revoked_grant = await db.get(ProviderTrustPermissionGrant, sub_grant_id)
            assert revoked_grant.revoked_at is not None

    finally:
        await engine.dispose()


async def test_active_duplicate_denied_and_expired_unrevoked_supersedes(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        # 1. Create active grant with valid_until in the future
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            await svc.apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                valid_until=now + timedelta(days=30),
                idempotency_key=_key("qual-active-dup-01"),
                now=now,
            )

        # 2. Duplicate active grant -> denied
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=manager_id,
                    authentication=auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    valid_until=now + timedelta(days=60),
                    idempotency_key=_key("qual-active-dup-02"),
                    now=now,
                )
            assert exc.value.code == "TRUST_PERMISSION_POLICY_DENIED"
            assert exc.value.policy_code == "ACTIVE_GRANT_EXISTS"

        # 3. Simulate passage of time: now is after valid_until
        future_now = now + timedelta(days=35)
        # Auth at future_now
        future_auth = TrustManagementAuthentication(
            provider_id=manager_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=future_now,
        )

        # 4. Expired unrevoked slot: now supersedes atomically!
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res_super = await svc.apply_grant(
                actor_id=manager_id,
                authentication=future_auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                valid_until=future_now + timedelta(days=30),
                idempotency_key=_key("qual-super-01"),
                now=future_now,
            )
            assert res_super.superseded_grant_id is not None
            assert len(res_super.event_types) == 2

        # Verify old row has revoked_at and new row is active
        async with factory() as db:
            old_grant = await db.get(
                ProviderTrustPermissionGrant, res_super.superseded_grant_id
            )
            assert old_grant.revoked_at == future_now
            new_grant = await db.get(ProviderTrustPermissionGrant, res_super.grant_id)
            assert new_grant.revoked_at is None

    finally:
        await engine.dispose()


async def test_idempotent_replay_and_conflict_grant_and_revoke(monkeypatch):
    """Gate 4: Full idempotency replay and conflict integrity for both grant and revoke."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        shared_grant_key = _key("qual-replay-grant")

        # 1. Initial grant
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res1 = await svc.apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=shared_grant_key,
                now=now,
            )
            assert not res1.idempotent_replay

        # 2. Exact same GRANT request replay
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            res2 = await svc.apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=shared_grant_key,
                now=now,
            )
            assert res2.idempotent_replay
            assert res2.grant_id == res1.grant_id

        # Verify idempotency record in PostgreSQL: resulting_resource_version must be NULL
        async with factory() as db:
            idem_row = (
                await db.execute(
                    text(
                        "SELECT resulting_resource_version, response_status, response_payload "
                        "FROM public.mutation_idempotency WHERE idempotency_key = :k"
                    ),
                    {"k": shared_grant_key},
                )
            ).first()
            assert idem_row.resulting_resource_version is None
            assert idem_row.response_status == 200
            assert "command" in idem_row.response_payload

        # 3. Same key with different GRANT semantics -> IDEMPOTENCY_KEY_REUSED
        other_target = await _create_provider(factory)
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=manager_id,
                    authentication=auth,
                    target_provider_id=other_target,  # Different target!
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=shared_grant_key,
                    now=now,
                )
            assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"

        # 4. Initial REVOKE
        shared_revoke_key = _key("qual-replay-revoke")
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            rev1 = await svc.apply_revoke(
                actor_id=manager_id,
                authentication=auth,
                grant_id=res1.grant_id,
                revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
                idempotency_key=shared_revoke_key,
                now=now,
            )
            assert not rev1.idempotent_replay

        # 5. Exact same REVOKE replay
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            rev2 = await svc.apply_revoke(
                actor_id=manager_id,
                authentication=auth,
                grant_id=res1.grant_id,
                revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
                idempotency_key=shared_revoke_key,
                now=now,
            )
            assert rev2.idempotent_replay
            assert rev2.grant_id == res1.grant_id

        # Verify revoke idempotency record in DB: resulting_resource_version must be NULL
        async with factory() as db:
            idem_rev_row = (
                await db.execute(
                    text(
                        "SELECT resulting_resource_version, response_status, response_payload "
                        "FROM public.mutation_idempotency WHERE idempotency_key = :k"
                    ),
                    {"k": shared_revoke_key},
                )
            ).first()
            assert idem_rev_row.resulting_resource_version is None
            assert idem_rev_row.response_status == 200

        # 6. Same key with different REVOKE semantics -> IDEMPOTENCY_KEY_REUSED
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_revoke(
                    actor_id=manager_id,
                    authentication=auth,
                    grant_id=res1.grant_id,
                    revocation_reason_code=RevocationReasonCode.SECURITY_RESPONSE,  # Different reason!
                    idempotency_key=shared_revoke_key,
                    now=now,
                )
            assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Gate 3: Atomicity Evidence Tests (A through F)
# ---------------------------------------------------------------------------


async def test_atomicity_grant_audit_failure_rolls_back(monkeypatch):
    """Gate 3A: Audit failure during grant rolls back grant, audit, and reservation."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)
        fail_key = _key("atom-grant-audit-fail")

        async with factory() as db:
            with patch(
                "app.services.provider_trust_permission_application.enqueue_audit_event",
                AsyncMock(side_effect=RuntimeError("simulated audit failure")),
            ):
                svc = ProviderTrustPermissionApplicationService(db)
                with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                    await svc.apply_grant(
                        actor_id=manager_id,
                        authentication=auth,
                        target_provider_id=target_id,
                        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                        idempotency_key=fail_key,
                        now=now,
                    )
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"

        # Verify nothing was committed: no grant, no audit, no completed or reserved idempotency
        async with factory() as db:
            grant_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.provider_trust_permission_grant WHERE provider_id = :p"
                ),
                {"p": target_id},
            )
            assert grant_count == 0

            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": fail_key},
            )
            assert idem_count == 0

    finally:
        await engine.dispose()


async def test_atomicity_revoke_audit_failure_rolls_back(monkeypatch):
    """Gate 3B: Audit failure during revoke rolls back revoked_at update and reservation."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        # Create active grant
        async with factory() as db:
            res_grant = await ProviderTrustPermissionApplicationService(db).apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=_key("seed-grant-for-revoke-fail"),
                now=now,
            )
            grant_id = res_grant.grant_id

        fail_revoke_key = _key("atom-revoke-audit-fail")

        async with factory() as db:
            with patch(
                "app.services.provider_trust_permission_application.enqueue_audit_event",
                AsyncMock(side_effect=RuntimeError("simulated audit failure")),
            ):
                svc = ProviderTrustPermissionApplicationService(db)
                with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                    await svc.apply_revoke(
                        actor_id=manager_id,
                        authentication=auth,
                        grant_id=grant_id,
                        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
                        idempotency_key=fail_revoke_key,
                        now=now,
                    )
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"

        # Grant must remain UNREVOKED; no idempotency row
        async with factory() as db:
            grant_row = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert grant_row.revoked_at is None

            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": fail_revoke_key},
            )
            assert idem_count == 0

    finally:
        await engine.dispose()


async def test_atomicity_supersession_audit_failure_rolls_back(monkeypatch):
    """Gate 3C: Audit failure during supersession preserves expired row unrevoked, no replacement, no reservation."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)
        expired_grant_id = uuid.uuid4()

        # Seed expired unrevoked grant
        async with factory() as db:
            db.add(
                ProviderTrustPermissionGrant(
                    id=expired_grant_id,
                    provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,
                    scope_type=TrustPermissionScope.GLOBAL.value,
                    facility_id=None,
                    granted_at=now - timedelta(days=60),
                    valid_from=now - timedelta(days=60),
                    valid_until=now - timedelta(days=10),  # Expired!
                    revoked_at=None,  # Unrevoked!
                    granted_by_actor_id="test-seed",
                )
            )
            await db.commit()

        fail_super_key = _key("atom-super-audit-fail")

        async with factory() as db:
            with patch(
                "app.services.provider_trust_permission_application.enqueue_audit_event",
                AsyncMock(
                    side_effect=RuntimeError("simulated supersession audit failure")
                ),
            ):
                svc = ProviderTrustPermissionApplicationService(db)
                with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                    await svc.apply_grant(
                        actor_id=manager_id,
                        authentication=auth,
                        target_provider_id=target_id,
                        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                        valid_until=now + timedelta(days=30),
                        idempotency_key=fail_super_key,
                        now=now,
                    )
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"

        # Old row must NOT be left revoked; no replacement grant; no idempotency row
        async with factory() as db:
            old_row = await db.get(ProviderTrustPermissionGrant, expired_grant_id)
            assert old_row.revoked_at is None

            all_grants = (
                await db.scalars(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == target_id
                    )
                )
            ).all()
            assert len(all_grants) == 1
            assert all_grants[0].id == expired_grant_id

            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": fail_super_key},
            )
            assert idem_count == 0

    finally:
        await engine.dispose()


async def test_atomicity_idempotency_completion_failure_rolls_back(monkeypatch):
    """Gate 3D: Completion failure rolls back mutation, audit staging, and reservation."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        # Grant seed row
        async with factory() as db:
            res_grant = await ProviderTrustPermissionApplicationService(db).apply_grant(
                actor_id=manager_id,
                authentication=auth,
                target_provider_id=target_id,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=_key("seed-for-idem-comp-fail"),
                now=now,
            )
            grant_id = res_grant.grant_id

        fail_idem_key = _key("atom-idem-comp-fail")

        async with factory() as db:
            with patch(
                "app.services.provider_trust_permission_application._IDEMPOTENCY_COMPLETE",
                text("SELECT 1 / 0"),
            ):
                svc = ProviderTrustPermissionApplicationService(db)
                with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                    await svc.apply_revoke(
                        actor_id=manager_id,
                        authentication=auth,
                        grant_id=grant_id,
                        revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
                        idempotency_key=fail_idem_key,
                        now=now,
                    )
                assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"

        # Revoked_at remains NULL, no idempotency row
        async with factory() as db:
            grant_row = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert grant_row.revoked_at is None

            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": fail_idem_key},
            )
            assert idem_count == 0

    finally:
        await engine.dispose()


async def test_atomicity_authorization_denial_leaves_no_reservation(monkeypatch):
    """Gate 3E: Authorization denial after reservation rolls back the reservation."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        non_manager_id = await _create_provider(factory)
        target_id = await _create_provider(factory)
        now = datetime.now(timezone.utc)
        non_manager_auth = TrustManagementAuthentication(
            provider_id=non_manager_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=now,
        )

        auth_deny_key = _key("atom-auth-deny-no-res")

        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=non_manager_id,
                    authentication=non_manager_auth,
                    target_provider_id=target_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=auth_deny_key,
                    now=now,
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

        # Reservation must have rolled back with the transaction
        async with factory() as db:
            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": auth_deny_key},
            )
            assert idem_count == 0

    finally:
        await engine.dispose()


async def test_atomicity_policy_denial_leaves_no_reservation(monkeypatch):
    """Gate 3F: Pure policy denial after reservation rolls back the reservation."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        manager_id, auth = await _create_root_manager(factory)
        now = datetime.now(timezone.utc)

        self_grant_key = _key("atom-policy-deny-no-res")

        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=manager_id,
                    authentication=auth,
                    target_provider_id=manager_id,  # Self grant -> denied!
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=self_grant_key,
                    now=now,
                )
            assert exc.value.code == "TRUST_PERMISSION_POLICY_DENIED"
            assert exc.value.policy_code == "SELF_GRANT_PROHIBITED"

        # Reservation must have rolled back with the transaction
        async with factory() as db:
            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k"
                ),
                {"k": self_grant_key},
            )
            assert idem_count == 0

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Concurrency Proof 1: Duplicate Grant Race
# ---------------------------------------------------------------------------


async def test_concurrency_duplicate_grant_race(monkeypatch):
    """Two independent managers concurrently grant the same slot with different keys."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_a, auth_a = await _create_root_manager(factory)
        mgr_b, auth_b = await _create_root_manager(factory)
        target = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        key_a = _key("race-dup-grant-a")
        key_b = _key("race-dup-grant-b")

        async def _run_a():
            async with factory() as db:
                return await ProviderTrustPermissionApplicationService(db).apply_grant(
                    actor_id=mgr_a,
                    authentication=auth_a,
                    target_provider_id=target,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=key_a,
                    now=now,
                )

        async def _run_b():
            async with factory() as db:
                return await ProviderTrustPermissionApplicationService(db).apply_grant(
                    actor_id=mgr_b,
                    authentication=auth_b,
                    target_provider_id=target,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=key_b,
                    now=now,
                )

        results = await asyncio.gather(_run_a(), _run_b(), return_exceptions=True)
        successes = [
            r
            for r in results
            if isinstance(r, ProviderTrustPermissionApplicationResult)
        ]
        errors = [
            r for r in results if isinstance(r, ProviderTrustPermissionApplicationError)
        ]

        assert len(successes) == 1
        assert len(errors) == 1
        assert errors[0].code == "TRUST_PERMISSION_POLICY_DENIED"
        assert errors[0].policy_code == "ACTIVE_GRANT_EXISTS"

        # Exactly 1 unrevoked grant row
        async with factory() as db:
            grants = (
                await db.scalars(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == target
                    )
                )
            ).all()
            assert len(grants) == 1
            assert grants[0].revoked_at is None

            # Exactly 1 grant audit outbox event
            outbox_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.audit_outbox WHERE payload -> 'metadata' ->> 'command' = 'GRANT' AND payload -> 'metadata' ->> 'target_provider_id' = :p"
                ),
                {"p": str(target)},
            )
            assert outbox_count == 1

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Concurrency Proof 2: Reciprocal Managers (Deadlock Free)
# ---------------------------------------------------------------------------


async def test_concurrency_reciprocal_managers_deadlock_free(monkeypatch):
    """Manager A grants to Manager B while Manager B grants to Manager A."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_a, auth_a = await _create_root_manager(factory)
        mgr_b, auth_b = await _create_root_manager(factory)
        now = datetime.now(timezone.utc)

        key_a2b = _key("race-recip-a2b")
        key_b2a = _key("race-recip-b2a")

        # Concurrently: A grants to B, B grants to A
        async def _grant_a_to_b():
            async with factory() as db:
                return await ProviderTrustPermissionApplicationService(db).apply_grant(
                    actor_id=mgr_a,
                    authentication=auth_a,
                    target_provider_id=mgr_b,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=key_a2b,
                    now=now,
                )

        async def _grant_b_to_a():
            async with factory() as db:
                return await ProviderTrustPermissionApplicationService(db).apply_grant(
                    actor_id=mgr_b,
                    authentication=auth_b,
                    target_provider_id=mgr_a,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=key_b2a,
                    now=now,
                )

        results = await asyncio.gather(
            _grant_a_to_b(), _grant_b_to_a(), return_exceptions=True
        )
        for r in results:
            assert isinstance(r, ProviderTrustPermissionApplicationResult)

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Concurrency Proof 3: Root Revocation Race (Both Linearizations: A and B)
# ---------------------------------------------------------------------------


async def test_concurrency_root_revocation_race_scenario_a(monkeypatch):
    """Gate 1 / Scenario A: Root revocation commits first, manager grant attempt fails."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_a, auth_a = await _create_root_manager(factory)
        target_a = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        # Offline governance revokes root authority using compatible lock discipline
        await _offline_governance_revoke_root(factory, mgr_a, now)

        # Manager attempts subordinate grant -> immediately rejected
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=mgr_a,
                    authentication=auth_a,
                    target_provider_id=target_a,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=_key("race-root-revoked-attempt"),
                    now=now,
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

        # Zero subordinate grants created
        async with factory() as db:
            grants = (
                await db.scalars(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == target_a
                    )
                )
            ).all()
            assert len(grants) == 0

    finally:
        await engine.dispose()


async def test_concurrency_root_revocation_race_scenario_b(monkeypatch):
    """Gate 1 / Scenario B: Manager M locks current authority first -> root revocation blocks -> M commits -> revocation commits -> M linearized first."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_m, auth_m = await _create_root_manager(factory)
        target_sub = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        m_holding_lock_event = asyncio.Event()
        allow_m_commit_event = asyncio.Event()

        # Wrap enqueue_audit_event: it is called while M holds the row locks inside its transaction
        real_enqueue = app_module.enqueue_audit_event

        async def _sync_enqueue(*args, **kwargs):
            m_holding_lock_event.set()
            await allow_m_commit_event.wait()
            return await real_enqueue(*args, **kwargs)

        grant_key = _key("race-scenario-b-grant")

        async def _run_manager_grant():
            async with factory() as db:
                svc = ProviderTrustPermissionApplicationService(db)
                with patch(
                    "app.services.provider_trust_permission_application.enqueue_audit_event",
                    _sync_enqueue,
                ):
                    return await svc.apply_grant(
                        actor_id=mgr_m,
                        authentication=auth_m,
                        target_provider_id=target_sub,
                        permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                        idempotency_key=grant_key,
                        now=now,
                    )

        # 1. Start Manager M's subordinate grant
        m_task = asyncio.create_task(_run_manager_grant())

        # 2. Wait until M has acquired row locks and evaluated authority
        await m_holding_lock_event.wait()

        # 3. Start offline root revocation task with compatible lock discipline
        # Because M holds the root grant lock FOR UPDATE, this task MUST block in PostgreSQL!
        revocation_task = asyncio.create_task(
            _offline_governance_revoke_root(factory, mgr_m, now)
        )

        # 4. Deterministically verify in PostgreSQL that root revocation is blocked waiting on the lock
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

        assert blocked_confirmed, "Offline root revocation must be blocked waiting on M's grant lock in PostgreSQL"

        # 5. Allow Manager M to proceed with audit, completion, and commit
        allow_m_commit_event.set()

        # Manager M completes and commits
        m_result = await m_task
        assert isinstance(m_result, ProviderTrustPermissionApplicationResult)
        assert m_result.permission == "PROFESSIONAL_REVIEW"

        # 6. Once M commits, root revocation unblocks and completes
        await revocation_task

        # 7. Assertions:
        # A. Exactly one subordinate grant exists for target
        async with factory() as db:
            sub_grants = (
                await db.scalars(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == target_sub
                    )
                )
            ).all()
            assert len(sub_grants) == 1
            assert sub_grants[0].revoked_at is None

            # B. Exactly one corresponding subordinate grant audit event exists
            outbox_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.audit_outbox WHERE payload -> 'metadata' ->> 'command' = 'GRANT' AND payload -> 'metadata' ->> 'target_provider_id' = :p"
                ),
                {"p": str(target_sub)},
            )
            assert outbox_count == 1

            # C. Root grant ultimately has revoked_at IS NOT NULL
            root_grant_row = (
                await db.execute(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == mgr_m,
                        ProviderTrustPermissionGrant.permission
                        == TrustManagementPermission.TRUST_PERMISSION_MANAGE.value,
                    )
                )
            ).scalar_one()
            assert root_grant_row.revoked_at is not None

        # D. Manager M's next operation returns AUTHORIZATION_DENIED
        target_2 = await _create_provider(factory)
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            with pytest.raises(ProviderTrustPermissionApplicationError) as exc:
                await svc.apply_grant(
                    actor_id=mgr_m,
                    authentication=auth_m,
                    target_provider_id=target_2,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=_key("race-scenario-b-subsequent-denied"),
                    now=now + timedelta(seconds=1),
                )
            assert exc.value.code == "AUTHORIZATION_DENIED"

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Concurrency Proof 4: Revoke Race
# ---------------------------------------------------------------------------


async def test_concurrency_revoke_race(monkeypatch):
    """Two managers concurrently revoke the same subordinate grant."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_a, auth_a = await _create_root_manager(factory)
        mgr_b, auth_b = await _create_root_manager(factory)
        target = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        # Grant first
        async with factory() as db:
            res = await ProviderTrustPermissionApplicationService(db).apply_grant(
                actor_id=mgr_a,
                authentication=auth_a,
                target_provider_id=target,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=_key("race-revoke-seed"),
                now=now,
            )
            grant_id = res.grant_id

        key_a = _key("race-revoke-a")
        key_b = _key("race-revoke-b")

        # Concurrently revoke
        async def _revoke_a():
            async with factory() as db:
                return await ProviderTrustPermissionApplicationService(db).apply_revoke(
                    actor_id=mgr_a,
                    authentication=auth_a,
                    grant_id=grant_id,
                    revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
                    idempotency_key=key_a,
                    now=now,
                )

        async def _revoke_b():
            async with factory() as db:
                return await ProviderTrustPermissionApplicationService(db).apply_revoke(
                    actor_id=mgr_b,
                    authentication=auth_b,
                    grant_id=grant_id,
                    revocation_reason_code=RevocationReasonCode.ROLE_CHANGED,
                    idempotency_key=key_b,
                    now=now,
                )

        results = await asyncio.gather(_revoke_a(), _revoke_b(), return_exceptions=True)
        successes = [
            r
            for r in results
            if isinstance(r, ProviderTrustPermissionApplicationResult)
        ]
        errors = [
            r for r in results if isinstance(r, ProviderTrustPermissionApplicationError)
        ]

        assert len(successes) == 1
        assert len(errors) == 1
        assert errors[0].code == "TRUST_PERMISSION_POLICY_DENIED"
        assert errors[0].policy_code == "GRANT_ALREADY_REVOKED"

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Concurrency Proof 5: Idempotency Race
# ---------------------------------------------------------------------------


async def test_concurrency_idempotency_race(monkeypatch):
    """Two concurrent calls with exact same idempotency key and request."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr_a, auth_a = await _create_root_manager(factory)
        target = await _create_provider(factory)
        now = datetime.now(timezone.utc)

        shared_key = _key("race-same-key-idem")

        async def _call():
            async with factory() as db:
                return await ProviderTrustPermissionApplicationService(db).apply_grant(
                    actor_id=mgr_a,
                    authentication=auth_a,
                    target_provider_id=target,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                    idempotency_key=shared_key,
                    now=now,
                )

        results = await asyncio.gather(_call(), _call(), return_exceptions=True)
        for r in results:
            assert isinstance(r, ProviderTrustPermissionApplicationResult)

        # Exactly 1 unrevoked grant row
        async with factory() as db:
            grants = (
                await db.scalars(
                    select(ProviderTrustPermissionGrant).where(
                        ProviderTrustPermissionGrant.provider_id == target
                    )
                )
            ).all()
            assert len(grants) == 1

            # Exactly 1 completed idempotency row
            idem_count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = :k AND response_status = 200"
                ),
                {"k": shared_key},
            )
            assert idem_count == 1

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Clinical Authority Separation Regression Proof
# ---------------------------------------------------------------------------


async def test_clinical_authority_separation_regression(monkeypatch):
    """Granting PROFESSIONAL_REVIEW never grants clinical authority or alters clinical capabilities."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        mgr, auth = await _create_root_manager(factory)
        target = await _create_provider(factory)
        fac_id = await _create_facility(factory)
        now = datetime.now(timezone.utc)

        # Grant PROFESSIONAL_REVIEW to target
        async with factory() as db:
            svc = ProviderTrustPermissionApplicationService(db)
            await svc.apply_grant(
                actor_id=mgr,
                authentication=auth,
                target_provider_id=target,
                permission=TrustManagementPermission.PROFESSIONAL_REVIEW,
                idempotency_key=_key("qual-clin-sep-01"),
                now=now,
            )

        # Verify target provider's clinical eligibility: MUST STILL BE DENIED!
        async with factory() as db:
            target_identity = await db.get(ProviderIdentity, target)
            clin_service = ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            )
            clin_auth = InteractiveClinicalAuthentication(
                provider_id=target,
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
            # Must be denied because target has NO verified affiliation or clinical role
            assert not decision.allowed
            assert decision.denial_code is not None

    finally:
        await engine.dispose()
