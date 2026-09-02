"""Disposable PostgreSQL qualification for Phase-3D grant persistence."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.provider import (
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.security.trust_management_permissions import TrustManagementPermission
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.services.provider_trust_authorization import (
    ProviderTrustAuthorizationService,
    TrustAuthorizationDenialCode,
    TrustManagementAuthentication,
)


pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
PREVIOUS_HEAD = "20260903_trust_lifecycle"
CURRENT_HEAD = "20260903_trust_authorization"
_LEGACY_ROLES = (
    "admin",
    "privacy_officer",
    "auditor",
    "clinical_reviewer",
    "clinician",
    "receptionist",
)


def _url(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        pytest.skip(f"{name} is not configured")
    if "127.0.0.1" not in value and "localhost" not in value:
        pytest.fail(f"{name} must be loopback-only")
    if "nexa_qual_" not in value:
        pytest.fail(f"{name} must be disposable")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.mark.asyncio
async def test_fresh_and_previous_head_authorization_migration(monkeypatch):
    fresh = _url("TRUST_AUTH_FRESH_DATABASE_URL")
    previous = _url("TRUST_AUTH_PREVIOUS_DATABASE_URL")
    monkeypatch.setenv("TEST_DATABASE_URL", fresh)
    await asyncio.to_thread(command.upgrade, _config(fresh), CURRENT_HEAD)
    fresh_engine = create_async_engine(fresh)
    try:
        async with async_sessionmaker(fresh_engine)() as db:
            assert (
                await db.scalar(text("SELECT version_num FROM alembic_version"))
                == CURRENT_HEAD
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM provider_trust_permission_grant")
                )
                == 0
            )
    finally:
        await fresh_engine.dispose()

    monkeypatch.setenv("TEST_DATABASE_URL", previous)
    await asyncio.to_thread(command.upgrade, _config(previous), PREVIOUS_HEAD)
    engine = create_async_engine(previous)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider_ids = {role: uuid.uuid4() for role in _LEGACY_ROLES}
    provider_id, facility_id = provider_ids["admin"], uuid.uuid4()
    try:
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO hospital_registry (id, facility_code, legal_name, display_name, country_code, is_active, created_at, updated_at) VALUES (:id, :code, 'Synthetic', 'Synthetic', 'IN', TRUE, now(), now())"
                ),
                {"id": facility_id, "code": f"AUTH-{facility_id.hex[:12]}"},
            )
            for role, legacy_provider_id in provider_ids.items():
                await db.execute(
                    text(
                        "INSERT INTO provider_identity (id, provider_uid, role, status, is_active, created_at, updated_at) VALUES (:id, :uid, :role, 'active', TRUE, now(), now())"
                    ),
                    {
                        "id": legacy_provider_id,
                        "uid": f"legacy-{role}-{legacy_provider_id.hex}",
                        "role": role,
                    },
                )
            await db.commit()
        await asyncio.to_thread(command.upgrade, _config(previous), CURRENT_HEAD)
        async with factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM provider_trust_permission_grant")
                )
                == 0
            )
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM provider_identity WHERE role = ANY(:roles)"
                    ),
                    {"roles": list(_LEGACY_ROLES)},
                )
            ) >= len(_LEGACY_ROLES)
            await db.execute(
                text(
                    "INSERT INTO provider_trust_permission_grant (id, provider_id, permission, scope_type, facility_id, granted_by_actor_id, granted_at, created_at, updated_at) VALUES (gen_random_uuid(), :provider, 'FACILITY_REVIEW', 'FACILITY', :facility, 'governance', now(), now(), now())"
                ),
                {"provider": provider_id, "facility": facility_id},
            )
            await db.commit()
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(
                    text(
                        "INSERT INTO provider_trust_permission_grant (id, provider_id, permission, scope_type, facility_id, granted_by_actor_id, granted_at, created_at, updated_at) VALUES (gen_random_uuid(), :provider, 'FACILITY_REVIEW', 'FACILITY', :facility, 'governance', now(), now(), now())"
                    ),
                    {"provider": provider_id, "facility": facility_id},
                )
                await db.commit()
            await db.rollback()
            await db.execute(
                text(
                    "UPDATE provider_trust_permission_grant SET revoked_at = now() WHERE provider_id = :provider"
                ),
                {"provider": provider_id},
            )
            await db.commit()
            await db.execute(
                text(
                    "INSERT INTO provider_trust_permission_grant (id, provider_id, permission, scope_type, facility_id, granted_by_actor_id, granted_at, created_at, updated_at) VALUES (gen_random_uuid(), :provider, 'FACILITY_REVIEW', 'FACILITY', :facility, 'governance', now(), now(), now())"
                ),
                {"provider": provider_id, "facility": facility_id},
            )
            await db.commit()
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM provider_trust_permission_grant WHERE provider_id = :provider"
                    ),
                    {"provider": provider_id},
                )
            ) == 2
            await db.execute(
                text(
                    "UPDATE provider_trust_permission_grant SET valid_until = now() WHERE provider_id = :provider AND revoked_at IS NULL"
                ),
                {"provider": provider_id},
            )
            await db.commit()
            with pytest.raises(IntegrityError):
                await db.execute(
                    text(
                        "INSERT INTO provider_trust_permission_grant (id, provider_id, permission, scope_type, facility_id, granted_by_actor_id, granted_at, created_at, updated_at) VALUES (gen_random_uuid(), :provider, 'FACILITY_REVIEW', 'FACILITY', :facility, 'governance', now(), now(), now())"
                    ),
                    {"provider": provider_id, "facility": facility_id},
                )
                await db.commit()
            await db.rollback()
            with pytest.raises(IntegrityError):
                await db.execute(
                    text(
                        "INSERT INTO provider_trust_permission_grant (id, provider_id, permission, scope_type, facility_id, granted_by_actor_id, granted_at, created_at, updated_at) VALUES (gen_random_uuid(), :provider, 'PROFESSIONAL_REVIEW', 'FACILITY', :facility, 'governance', now(), now(), now())"
                    ),
                    {"provider": provider_id, "facility": facility_id},
                )
                await db.commit()
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_postgresql_grant_revocation_denies_the_next_evaluation(
    monkeypatch,
):
    url = _url("TRUST_AUTH_FRESH_DATABASE_URL")
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), CURRENT_HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    provider_id, facility_id = uuid.uuid4(), uuid.uuid4()
    provider_uid = f"trust-reviewer-{provider_id.hex}"
    try:
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO hospital_registry (id, facility_code, legal_name, display_name, country_code, is_active, created_at, updated_at) VALUES (:id, :code, 'Synthetic', 'Synthetic', 'IN', TRUE, now(), now())"
                ),
                {"id": facility_id, "code": f"AUTH-{facility_id.hex[:12]}"},
            )
            provider = ProviderIdentity(
                id=provider_id,
                provider_uid=provider_uid,
                hospital_id=facility_id,
                contact_email=f"{provider_uid}@example.test",
                contact_phone="+910000000000",
                email_verified_at=now,
                phone_verified_at=now,
                status="active",
                is_active=True,
                role="admin",
            )
            credential = ProviderCredential(
                provider_id=provider_id,
                provider_uid=provider_uid,
                login_identifier=f"{provider_uid}@example.test",
                password_hash="synthetic-not-a-secret",
                mfa_enabled=True,
                is_active=True,
            )
            db.add_all((provider, credential))
            db.add(
                ProviderTrustPermissionGrant(
                    provider_id=provider_id,
                    permission=TrustManagementPermission.PROFESSIONAL_REVIEW.value,
                    scope_type="GLOBAL",
                    granted_by_actor_id="synthetic-governance",
                )
            )
            await db.commit()

            authentication = TrustManagementAuthentication(
                provider_id=provider_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=now - timedelta(seconds=1),
            )
            authorization = ProviderTrustAuthorizationService()
            assert (
                await authorization.authorize_professional_review(
                    db,
                    actor_id=provider_id,
                    target_provider_id=uuid.uuid4(),
                    authentication=authentication,
                    now=now,
                )
            ).allowed

            clinical = await ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            ).evaluate_interactive(
                db,
                provider,
                InteractiveClinicalAuthentication(
                    provider_id=provider_id,
                    hospital_id=facility_id,
                    method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                    session_authenticated=True,
                    mfa_verified_at=now - timedelta(seconds=1),
                ),
                ClinicalCapability.RECORD_READ,
                now=now,
            )
            assert clinical.allowed is False

            await db.execute(
                text(
                    "UPDATE provider_trust_permission_grant SET revoked_at = now() WHERE provider_id = :provider"
                ),
                {"provider": provider_id},
            )
            await db.commit()
            denied = await authorization.authorize_professional_review(
                db,
                actor_id=provider_id,
                target_provider_id=uuid.uuid4(),
                authentication=authentication,
                now=now,
            )
            assert denied.allowed is False
            assert (
                denied.denial_code
                is TrustAuthorizationDenialCode.TRUST_PERMISSION_REVOKED_OR_INACTIVE
            )
    finally:
        await engine.dispose()
