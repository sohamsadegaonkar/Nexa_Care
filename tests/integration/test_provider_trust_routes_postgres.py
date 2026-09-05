"""Disposable PostgreSQL HTTP-to-Phase-3E qualification for Phase 3F."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v2.provider_trust_routes import (
    ProviderTrustRouteError,
    provider_trust_route_error_response,
    router,
)
from app.core.database import get_db_session
from app.core.dependencies import (
    ProviderTrustRoutePrincipal,
    get_provider_trust_route_principal,
)
from app.models.provider import (
    FacilityVerification,
    HospitalRegistry,
    ProfessionalVerification,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.provider_trust_authorization import TrustManagementAuthentication


pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
HEAD = "20260905_verification_application"


def _url() -> str:
    value = os.getenv("TRUST_LIFECYCLE_DATABASE_URL", "")
    if not value:
        pytest.skip("TRUST_LIFECYCLE_DATABASE_URL is not configured")
    if "127.0.0.1" not in value or "nexa_qual_" not in value:
        pytest.fail("qualification database must be disposable and loopback-only")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _principal(provider_id: uuid.UUID, now: datetime) -> ProviderTrustRoutePrincipal:
    return ProviderTrustRoutePrincipal(
        actor_provider_id=provider_id,
        authentication=TrustManagementAuthentication(
            provider_id=provider_id,
            method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
            session_authenticated=True,
            mfa_verified_at=now,
        ),
    )


async def _outbox_count(db, target_id: uuid.UUID) -> int:
    return await db.scalar(
        text(
            "SELECT count(*) FROM public.audit_outbox "
            "WHERE payload ->> 'target_id' = :target_id"
        ),
        {"target_id": str(target_id)},
    )


async def _seed(factory):
    now = datetime.now(timezone.utc)
    facility_id = uuid.uuid4()
    self_id, subject_id, reviewer_id, affiliation_subject_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    (
        self_verification_id,
        subject_verification_id,
        facility_verification_id,
        affiliation_id,
    ) = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=facility_id,
                facility_code=f"F3F-{facility_id.hex[:12]}",
                legal_name="Synthetic Phase 3F Facility",
                display_name="Synthetic Phase 3F Facility",
                country_code="IN",
                is_active=True,
            )
        )

        def trusted_provider(provider_id: uuid.UUID, prefix: str) -> tuple:
            email = f"{prefix}-{provider_id.hex}@example.test"
            return (
                ProviderIdentity(
                    id=provider_id,
                    provider_uid=f"{prefix}-{provider_id.hex}",
                    hospital_id=facility_id,
                    contact_email=email,
                    contact_phone="+910000000000",
                    email_verified_at=now,
                    phone_verified_at=now,
                    status="active",
                    is_active=True,
                ),
                ProviderCredential(
                    provider_id=provider_id,
                    login_identifier=email,
                    password_hash="synthetic-not-a-secret",
                    mfa_enabled=True,
                    is_active=True,
                ),
            )

        db.add_all(
            (
                *trusted_provider(self_id, "self"),
                *trusted_provider(reviewer_id, "reviewer"),
                ProviderIdentity(
                    id=subject_id,
                    provider_uid=f"subject-{subject_id.hex}",
                    status="active",
                    is_active=True,
                ),
                ProviderIdentity(
                    id=affiliation_subject_id,
                    provider_uid=f"affiliation-subject-{affiliation_subject_id.hex}",
                    status="active",
                    is_active=True,
                ),
                ProfessionalVerification(
                    id=self_verification_id,
                    provider_id=self_id,
                    status="NOT_SUBMITTED",
                    version=1,
                    previous_verification_valid=False,
                ),
                ProfessionalVerification(
                    id=subject_verification_id,
                    provider_id=subject_id,
                    registration_authority_code="AUTH",
                    registration_number_normalized=f"SUBJECT-{subject_id.hex}",
                    status="PENDING_REVIEW",
                    version=1,
                    previous_verification_valid=False,
                ),
                FacilityVerification(
                    id=facility_verification_id,
                    facility_id=facility_id,
                    status="DRAFT",
                    version=1,
                ),
                ProviderHospitalAffiliation(
                    id=affiliation_id,
                    provider_id=affiliation_subject_id,
                    hospital_id=facility_id,
                    roles=[],
                    trust_status="PENDING_ACTIVATION",
                    version=1,
                ),
                ProviderTrustPermissionGrant(
                    provider_id=reviewer_id,
                    permission="PROFESSIONAL_REVIEW",
                    scope_type="GLOBAL",
                    facility_id=None,
                    granted_by_actor_id="synthetic-governance",
                ),
                ProviderTrustPermissionGrant(
                    provider_id=reviewer_id,
                    permission="FACILITY_REVIEW",
                    scope_type="FACILITY",
                    facility_id=facility_id,
                    granted_by_actor_id="synthetic-governance",
                ),
                ProviderTrustPermissionGrant(
                    provider_id=reviewer_id,
                    permission="AFFILIATION_MANAGE",
                    scope_type="FACILITY",
                    facility_id=facility_id,
                    granted_by_actor_id="synthetic-governance",
                ),
            )
        )
        await db.commit()
    return {
        "now": now,
        "facility_id": facility_id,
        "self_id": self_id,
        "reviewer_id": reviewer_id,
        "self_verification_id": self_verification_id,
        "subject_id": subject_id,
        "subject_verification_id": subject_verification_id,
        "facility_verification_id": facility_verification_id,
        "affiliation_id": affiliation_id,
    }


async def test_http_routes_close_lookup_transaction_before_real_phase3e_mutation(
    monkeypatch,
):
    """Business-ID lookup and Phase 3E's owner transaction stay separate."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        seeded = await _seed(factory)
        app = FastAPI()
        app.include_router(router)
        app.add_exception_handler(
            ProviderTrustRouteError, provider_trust_route_error_response
        )

        async def database_dependency():
            async with factory() as db:
                yield db

        active_principal = _principal(seeded["self_id"], seeded["now"])

        async def principal_dependency():
            return active_principal

        app.dependency_overrides[get_db_session] = database_dependency
        app.dependency_overrides[get_provider_trust_route_principal] = (
            principal_dependency
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            self_key = f"f3f-self-{uuid.uuid4().hex}"
            self_payload = {
                "expected_version": 1,
                "registration_authority_code": "AUTH",
                "registration_number": f"SELF-REG-{seeded['self_id'].hex}",
            }
            submitted = await client.post(
                "/api/v2/provider-trust/professional/me/submit",
                headers={"Idempotency-Key": self_key},
                json=self_payload,
            )
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["resource_id"] == str(
                seeded["self_verification_id"]
            )
            assert submitted.json()["new_state"] == "PENDING_REVIEW"

            replay = await client.post(
                "/api/v2/provider-trust/professional/me/submit",
                headers={"Idempotency-Key": self_key},
                json=self_payload,
            )
            assert (
                replay.status_code == 200 and replay.json()["idempotent_replay"] is True
            )
            key_reused = await client.post(
                "/api/v2/provider-trust/professional/me/submit",
                headers={"Idempotency-Key": self_key},
                json={**self_payload, "registration_number": "SELF-REG-002"},
            )
            assert key_reused.status_code == 409
            assert key_reused.json() == {"error_code": "IDEMPOTENCY_KEY_REUSED"}

            active_principal = _principal(seeded["reviewer_id"], seeded["now"])
            verified = await client.post(
                f"/api/v2/provider-trust/professional/{seeded['subject_id']}/verify",
                headers={"Idempotency-Key": f"f3f-verify-{uuid.uuid4().hex}"},
                json={
                    "expected_version": 1,
                    "registration_authority_code": "AUTH",
                    "registration_number_normalized": (
                        f"SUBJECT-VERIFIED-{seeded['subject_id'].hex}"
                    ),
                    "verification_method": "synthetic-review",
                    "verification_source": "synthetic-source",
                    "verification_reference": "synthetic-reference",
                    "identity_binding_method": "synthetic-binding",
                    "identity_binding_status": "MATCHED",
                },
            )
            assert verified.status_code == 200, verified.text

            facility = await client.post(
                f"/api/v2/provider-trust/facilities/{seeded['facility_id']}/submit",
                headers={
                    "Idempotency-Key": f"f3f-facility-{uuid.uuid4().hex}",
                    "X-Hospital-Id": str(uuid.uuid4()),
                },
                json={"expected_version": 1},
            )
            assert facility.status_code == 200, facility.text

            affiliation = await client.post(
                f"/api/v2/provider-trust/affiliations/{seeded['affiliation_id']}/activate",
                headers={"Idempotency-Key": f"f3f-affiliation-{uuid.uuid4().hex}"},
                json={"expected_version": 1, "valid_from": seeded["now"].isoformat()},
            )
            assert affiliation.status_code == 200, affiliation.text

        async with factory() as db:
            self_row = await db.get(
                ProfessionalVerification, seeded["self_verification_id"]
            )
            subject_row = await db.get(
                ProfessionalVerification, seeded["subject_verification_id"]
            )
            facility_row = await db.get(
                FacilityVerification, seeded["facility_verification_id"]
            )
            affiliation_row = await db.get(
                ProviderHospitalAffiliation, seeded["affiliation_id"]
            )
            assert self_row.status == "PENDING_REVIEW" and self_row.version == 2
            assert subject_row.status == "VERIFIED" and subject_row.version == 2
            assert subject_row.reviewer_id == str(seeded["reviewer_id"])
            assert (
                facility_row.status == "PENDING_VERIFICATION"
                and facility_row.version == 2
            )
            assert (
                affiliation_row.trust_status == "ACTIVE"
                and affiliation_row.version == 2
            )
            assert affiliation_row.roles == []
            for target_id in (
                seeded["self_verification_id"],
                seeded["subject_verification_id"],
                seeded["facility_verification_id"],
                seeded["affiliation_id"],
            ):
                assert await _outbox_count(db, target_id) == 1
            assert (
                await db.scalar(
                    select(ProviderTrustPermissionGrant.id).where(
                        ProviderTrustPermissionGrant.provider_id
                        == seeded["reviewer_id"]
                    )
                )
                is not None
            )
    finally:
        await engine.dispose()


async def test_http_scope_denials_and_recheck_due_never_persist_grace(monkeypatch):
    """Real HTTP proves scopes, self-management denial, and no-grace recheck."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        facility_a, facility_b = uuid.uuid4(), uuid.uuid4()
        reviewer_id, no_grant_id, wrong_scope_id = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        (
            review_subject_id,
            recheck_subject_id,
            affiliation_subject_id,
            cross_subject_id,
        ) = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        review_verification_id, recheck_verification_id, facility_verification_id = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        affiliation_id, own_affiliation_id, cross_affiliation_id = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )

        async with factory() as db:
            db.add_all(
                (
                    HospitalRegistry(
                        id=facility_a,
                        facility_code=f"F3F-A-{facility_a.hex[:10]}",
                        legal_name="Synthetic Phase 3F A",
                        display_name="Synthetic Phase 3F A",
                        country_code="IN",
                        is_active=True,
                    ),
                    HospitalRegistry(
                        id=facility_b,
                        facility_code=f"F3F-B-{facility_b.hex[:10]}",
                        legal_name="Synthetic Phase 3F B",
                        display_name="Synthetic Phase 3F B",
                        country_code="IN",
                        is_active=True,
                    ),
                )
            )

            def trusted_actor(actor_id: uuid.UUID, label: str) -> tuple:
                email = f"{label}-{actor_id.hex}@example.test"
                return (
                    ProviderIdentity(
                        id=actor_id,
                        provider_uid=f"{label}-{actor_id.hex}",
                        hospital_id=facility_a,
                        contact_email=email,
                        contact_phone="+910000000000",
                        email_verified_at=now,
                        phone_verified_at=now,
                        status="active",
                        is_active=True,
                    ),
                    ProviderCredential(
                        provider_id=actor_id,
                        login_identifier=email,
                        password_hash="synthetic-not-a-secret",
                        mfa_enabled=True,
                        is_active=True,
                    ),
                )

            db.add_all(
                (
                    *trusted_actor(reviewer_id, "reviewer"),
                    *trusted_actor(no_grant_id, "legacy-role"),
                    *trusted_actor(wrong_scope_id, "wrong-scope"),
                    ProviderIdentity(
                        id=review_subject_id,
                        provider_uid=f"review-subject-{review_subject_id.hex}",
                        status="active",
                        is_active=True,
                    ),
                    ProviderIdentity(
                        id=recheck_subject_id,
                        provider_uid=f"recheck-subject-{recheck_subject_id.hex}",
                        status="active",
                        is_active=True,
                    ),
                    ProviderIdentity(
                        id=affiliation_subject_id,
                        provider_uid=f"affiliation-subject-{affiliation_subject_id.hex}",
                        status="active",
                        is_active=True,
                    ),
                    ProviderIdentity(
                        id=cross_subject_id,
                        provider_uid=f"cross-subject-{cross_subject_id.hex}",
                        status="active",
                        is_active=True,
                    ),
                    ProfessionalVerification(
                        id=review_verification_id,
                        provider_id=review_subject_id,
                        registration_authority_code="AUTH",
                        registration_number_normalized=f"REVIEW-{review_subject_id.hex}",
                        status="PENDING_REVIEW",
                        version=1,
                        previous_verification_valid=False,
                    ),
                    ProfessionalVerification(
                        id=recheck_verification_id,
                        provider_id=recheck_subject_id,
                        registration_authority_code="AUTH",
                        registration_number_normalized=f"RECHECK-{recheck_subject_id.hex}",
                        verification_method="synthetic-review",
                        verification_source="synthetic-source",
                        verification_reference="synthetic-reference",
                        identity_binding_method="synthetic-binding",
                        identity_binding_status="MATCHED",
                        verified_at=now,
                        last_checked_at=now,
                        registration_valid_until=now + timedelta(days=30),
                        status="VERIFIED",
                        version=1,
                        previous_verification_valid=True,
                    ),
                    FacilityVerification(
                        id=facility_verification_id,
                        facility_id=facility_a,
                        status="DRAFT",
                        version=1,
                    ),
                    ProviderHospitalAffiliation(
                        id=affiliation_id,
                        provider_id=affiliation_subject_id,
                        hospital_id=facility_a,
                        roles=["clinician"],
                        trust_status="PENDING_ACTIVATION",
                        version=1,
                    ),
                    ProviderHospitalAffiliation(
                        id=own_affiliation_id,
                        provider_id=reviewer_id,
                        hospital_id=facility_a,
                        roles=["admin"],
                        trust_status="PENDING_ACTIVATION",
                        version=1,
                    ),
                    ProviderHospitalAffiliation(
                        id=cross_affiliation_id,
                        provider_id=cross_subject_id,
                        hospital_id=facility_b,
                        roles=["clinician"],
                        trust_status="PENDING_ACTIVATION",
                        version=1,
                    ),
                    ProviderHospitalAffiliation(
                        provider_id=no_grant_id,
                        hospital_id=facility_a,
                        roles=["admin", "clinician", "clinical_reviewer"],
                        trust_status="ACTIVE",
                        version=1,
                    ),
                    ProviderTrustPermissionGrant(
                        provider_id=reviewer_id,
                        permission="PROFESSIONAL_REVIEW",
                        scope_type="GLOBAL",
                        facility_id=None,
                        granted_by_actor_id="synthetic-governance",
                    ),
                    ProviderTrustPermissionGrant(
                        provider_id=reviewer_id,
                        permission="FACILITY_REVIEW",
                        scope_type="FACILITY",
                        facility_id=facility_a,
                        granted_by_actor_id="synthetic-governance",
                    ),
                    ProviderTrustPermissionGrant(
                        provider_id=reviewer_id,
                        permission="AFFILIATION_MANAGE",
                        scope_type="FACILITY",
                        facility_id=facility_a,
                        granted_by_actor_id="synthetic-governance",
                    ),
                    ProviderTrustPermissionGrant(
                        provider_id=wrong_scope_id,
                        permission="FACILITY_REVIEW",
                        scope_type="FACILITY",
                        facility_id=facility_b,
                        granted_by_actor_id="synthetic-governance",
                    ),
                )
            )
            await db.commit()

        app = FastAPI()
        app.include_router(router)
        app.add_exception_handler(
            ProviderTrustRouteError, provider_trust_route_error_response
        )

        async def database_dependency():
            async with factory() as db:
                yield db

        active_principal = _principal(no_grant_id, now)

        async def principal_dependency():
            return active_principal

        app.dependency_overrides[get_db_session] = database_dependency
        app.dependency_overrides[get_provider_trust_route_principal] = (
            principal_dependency
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        evidence = {
            "expected_version": 1,
            "registration_authority_code": "AUTH",
            "registration_number_normalized": (
                f"REVIEW-VERIFIED-{review_subject_id.hex}"
            ),
            "verification_method": "synthetic-review",
            "verification_source": "synthetic-source",
            "verification_reference": "synthetic-reference",
            "identity_binding_method": "synthetic-binding",
            "identity_binding_status": "MATCHED",
        }
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            denied = await client.post(
                f"/api/v2/provider-trust/professional/{review_subject_id}/verify",
                headers={"Idempotency-Key": f"f3f-no-grant-{uuid.uuid4().hex}"},
                json=evidence,
            )
            assert denied.status_code == 403
            assert denied.json() == {"error_code": "AUTHORIZATION_DENIED"}

            active_principal = _principal(reviewer_id, now)
            reviewed = await client.post(
                f"/api/v2/provider-trust/professional/{review_subject_id}/verify",
                headers={"Idempotency-Key": f"f3f-review-{uuid.uuid4().hex}"},
                json=evidence,
            )
            assert reviewed.status_code == 200

            active_principal = _principal(wrong_scope_id, now)
            wrong_scope = await client.post(
                f"/api/v2/provider-trust/facilities/{facility_a}/submit",
                headers={"Idempotency-Key": f"f3f-wrong-facility-{uuid.uuid4().hex}"},
                json={"expected_version": 1},
            )
            assert wrong_scope.status_code == 403
            assert wrong_scope.json() == {"error_code": "AUTHORIZATION_DENIED"}

            active_principal = _principal(reviewer_id, now)
            facility = await client.post(
                f"/api/v2/provider-trust/facilities/{facility_a}/submit",
                headers={
                    "Idempotency-Key": f"f3f-facility-a-{uuid.uuid4().hex}",
                    "X-Hospital-Id": str(facility_b),
                },
                json={"expected_version": 1},
            )
            assert facility.status_code == 200

            own = await client.post(
                f"/api/v2/provider-trust/affiliations/{own_affiliation_id}/activate",
                headers={"Idempotency-Key": f"f3f-own-affiliation-{uuid.uuid4().hex}"},
                json={"expected_version": 1, "valid_from": now.isoformat()},
            )
            assert own.status_code == 403
            assert own.json() == {"error_code": "AUTHORIZATION_DENIED"}
            cross = await client.post(
                f"/api/v2/provider-trust/affiliations/{cross_affiliation_id}/activate",
                headers={
                    "Idempotency-Key": f"f3f-cross-affiliation-{uuid.uuid4().hex}"
                },
                json={"expected_version": 1, "valid_from": now.isoformat()},
            )
            assert cross.status_code == 403
            assert cross.json() == {"error_code": "AUTHORIZATION_DENIED"}
            affiliation = await client.post(
                f"/api/v2/provider-trust/affiliations/{affiliation_id}/activate",
                headers={"Idempotency-Key": f"f3f-affiliation-a-{uuid.uuid4().hex}"},
                json={"expected_version": 1, "valid_from": now.isoformat()},
            )
            assert affiliation.status_code == 200

            injection = await client.post(
                f"/api/v2/provider-trust/professional/{recheck_subject_id}/mark-recheck-due",
                headers={"Idempotency-Key": f"f3f-injected-recheck-{uuid.uuid4().hex}"},
                json={
                    "expected_version": 1,
                    "SOURCE_UNAVAILABLE": True,
                    "recheck_failure_reason": "SOURCE_UNAVAILABLE",
                    "grace_expires_at": (now + timedelta(hours=1)).isoformat(),
                    "previous_verification_valid": False,
                    "authoritative_adverse_signal_at": now.isoformat(),
                    "recheck_attempted_at": now.isoformat(),
                },
            )
            assert injection.status_code == 422
            assert "sqlalchemy" not in injection.text.lower()

            async with factory() as db:
                untouched = await db.get(
                    ProfessionalVerification, recheck_verification_id
                )
                assert untouched.status == "VERIFIED" and untouched.version == 1

            recheck = await client.post(
                f"/api/v2/provider-trust/professional/{recheck_subject_id}/mark-recheck-due",
                headers={"Idempotency-Key": f"f3f-recheck-{uuid.uuid4().hex}"},
                json={"expected_version": 1},
            )
            assert recheck.status_code == 200
            stale = await client.post(
                f"/api/v2/provider-trust/professional/{recheck_subject_id}/mark-recheck-due",
                headers={"Idempotency-Key": f"f3f-recheck-stale-{uuid.uuid4().hex}"},
                json={"expected_version": 1},
            )
            assert stale.status_code == 409
            assert stale.json() == {"error_code": "LIFECYCLE_VERSION_CONFLICT"}
            invalid_edge = await client.post(
                f"/api/v2/provider-trust/professional/{recheck_subject_id}/mark-recheck-due",
                headers={"Idempotency-Key": f"f3f-recheck-edge-{uuid.uuid4().hex}"},
                json={"expected_version": 2},
            )
            assert invalid_edge.status_code == 409
            assert invalid_edge.json() == {"error_code": "LIFECYCLE_POLICY_DENIED"}

        async with factory() as db:
            reviewed_row = await db.get(
                ProfessionalVerification, review_verification_id
            )
            recheck_row = await db.get(
                ProfessionalVerification, recheck_verification_id
            )
            facility_row = await db.get(FacilityVerification, facility_verification_id)
            affiliation_row = await db.get(ProviderHospitalAffiliation, affiliation_id)
            assert reviewed_row.reviewer_id == str(reviewer_id)
            assert reviewed_row.status == "VERIFIED" and reviewed_row.version == 2
            assert recheck_row.status == "RECHECK_DUE" and recheck_row.version == 2
            assert recheck_row.recheck_failure_reason is None
            assert recheck_row.grace_expires_at is None
            assert recheck_row.recheck_attempted_at is not None
            assert recheck_row.recheck_attempted_at >= now
            assert (
                facility_row.status == "PENDING_VERIFICATION"
                and facility_row.version == 2
            )
            assert (
                affiliation_row.trust_status == "ACTIVE"
                and affiliation_row.version == 2
            )
            assert affiliation_row.roles == ["clinician"]
            assert await _outbox_count(db, review_verification_id) == 1
            assert await _outbox_count(db, recheck_verification_id) == 1
            assert await _outbox_count(db, facility_verification_id) == 1
            assert await _outbox_count(db, affiliation_id) == 1
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.mutation_idempotency "
                        "WHERE resource_id = :resource_id AND response_status = 200"
                    ),
                    {"resource_id": str(recheck_verification_id)},
                )
                == 1
            )
    finally:
        await engine.dispose()
