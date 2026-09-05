"""Canonical disposable PostgreSQL/Redis E2E qualification for Phase 3G.

Final Slice-3 Adversarial and Security Qualification of the complete
provider registration and trust-management journey (Slices 3A through 3F).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import httpx
import pyotp
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import (
    get_async_engine,
    get_db_session,
    get_provider_contact_mutation_session,
    get_session_factory,
)
from app.core.redis import get_async_redis_client, get_redis_client
from app.core.security import encrypt_mfa_secret
from app.main import app
from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderContactVerificationChallenge,
    ProviderTrustPermissionGrant,
)
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.services.provider_auth_service import (
    hash_provider_password,
    issue_mfa_pending_token,
    issue_provider_session_token,
    verify_totp_code_once,
)
from app.services.provider_contact_assurance_service import (
    ProviderContactChallengeTransport,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.redis,
    pytest.mark.asyncio,
]

HEAD = "20260905_verification_application"
_USER_AGENT = "Nexa-Slice3G-Qualification-Agent/1.0"
_HMAC_SECRET = "synthetic-secret-for-provider-qualification-hmac-32bytes"


class CaptureTransport(ProviderContactChallengeTransport):
    """Synthetic capture transport seam for contact challenge verifiers."""

    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str, str]] = []

    def assert_ready(self) -> None:
        pass

    async def deliver(self, *, channel: str, destination: str, verifier: str) -> None:
        self.deliveries.append((channel, destination, verifier))


def _get_db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://nexa:nexa_test@127.0.0.1:55439/nexa_qual_provider_journey_3g",
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
def _setup_qualification_environment():
    db_url = _get_db_url()
    redis_url = _get_redis_url()

    os.environ["TEST_DATABASE_URL"] = db_url
    os.environ["DATABASE_URL"] = db_url
    os.environ["UPSTASH_REDIS_URL"] = redis_url
    os.environ["TEST_REDIS_URL"] = redis_url
    os.environ["TEST_REDIS_PREFIX"] = "nexa-qual-provider-journey-3g:"
    os.environ["OTP_RATE_LIMIT_HMAC_SECRET"] = _HMAC_SECRET
    os.environ["PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET"] = _HMAC_SECRET
    os.environ["PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET"] = _HMAC_SECRET
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

    # Verify migration to sole head on fresh database
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, HEAD)

    yield


@pytest.fixture(autouse=True)
async def override_deps(monkeypatch):
    """Shadow the global mock fixture: this qualification suite requires real PostgreSQL and Redis."""
    monkeypatch.setenv("TRUSTED_PROXY_NETWORKS", "127.0.0.1/32")
    app.dependency_overrides.clear()
    get_async_redis_client.cache_clear()
    get_redis_client.cache_clear()
    r = Redis.from_url(_get_redis_url(), decode_responses=True)
    await r.flushdb()
    await r.close()
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        get_async_redis_client.cache_clear()
        get_redis_client.cache_clear()


@pytest.fixture
def capture_transport(monkeypatch) -> CaptureTransport:
    transport = CaptureTransport()
    monkeypatch.setattr(
        "app.api.v2.auth_routes.get_provider_contact_challenge_transport",
        lambda: transport,
    )
    return transport


@pytest.fixture
async def db_factory():
    engine = create_async_engine(_get_db_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def qualification_client(db_factory):
    async def override_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_provider_contact_mutation_session] = override_db
    transport = httpx.ASGITransport(
        app=app, client=("127.0.0.1", 12345), raise_app_exceptions=False
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client
    app.dependency_overrides.pop(get_db_session, None)
    app.dependency_overrides.pop(get_provider_contact_mutation_session, None)


async def _seed_synthetic_fixture(factory) -> dict[str, uuid.UUID]:
    """Seed synthetic facilities and reviewer actors as a trusted governance precondition."""
    now = datetime.now(timezone.utc)
    facility_a_id = uuid.uuid4()
    facility_b_id = uuid.uuid4()
    prof_reviewer_id = uuid.uuid4()
    fac_reviewer_id = uuid.uuid4()
    affil_manager_id = uuid.uuid4()

    async with factory() as db:
        # 1. Facility A & Facility B in DRAFT
        db.add_all(
            [
                HospitalRegistry(
                    id=facility_a_id,
                    facility_code=f"FAC-A-{facility_a_id.hex[:8]}",
                    legal_name="Synthetic Facility Alpha",
                    display_name="Synthetic Facility Alpha",
                    country_code="IN",
                    is_active=True,
                ),
                HospitalRegistry(
                    id=facility_b_id,
                    facility_code=f"FAC-B-{facility_b_id.hex[:8]}",
                    legal_name="Synthetic Facility Beta",
                    display_name="Synthetic Facility Beta",
                    country_code="IN",
                    is_active=True,
                ),
                FacilityVerification(
                    id=uuid.uuid4(),
                    facility_id=facility_a_id,
                    status=FacilityVerificationStatus.DRAFT.value,
                    version=1,
                ),
                FacilityVerification(
                    id=uuid.uuid4(),
                    facility_id=facility_b_id,
                    status=FacilityVerificationStatus.DRAFT.value,
                    version=1,
                ),
            ]
        )

        def make_actor(actor_id: uuid.UUID, prefix: str) -> tuple:
            email = f"{prefix}-{actor_id.hex[:8]}@example.test"
            totp_sec = pyotp.random_base32()
            return (
                ProviderIdentity(
                    id=actor_id,
                    provider_uid=f"{prefix}-{actor_id.hex[:8]}",
                    hospital_id=facility_a_id,
                    contact_email=email,
                    contact_phone="+919876543210",
                    email_verified_at=now,
                    phone_verified_at=now,
                    status="active",
                    is_active=True,
                ),
                ProviderCredential(
                    provider_id=actor_id,
                    login_identifier=email,
                    password_hash=hash_provider_password("SyntheticPass123!"),
                    mfa_enabled=True,
                    mfa_secret_encrypted=encrypt_mfa_secret(totp_sec),
                    is_active=True,
                ),
                ProviderHospitalAffiliation(
                    id=uuid.uuid4(),
                    provider_id=actor_id,
                    hospital_id=facility_a_id,
                    affiliation_type="PERMANENT",
                    roles=[],
                    is_primary=True,
                    is_active=True,
                    trust_status=AffiliationTrustStatus.ACTIVE.value,
                    version=1,
                ),
                totp_sec,
            )

        p_ident, p_cred, p_affil, p_totp = make_actor(prof_reviewer_id, "prof-reviewer")
        f_ident, f_cred, f_affil, f_totp = make_actor(fac_reviewer_id, "fac-reviewer")
        a_ident, a_cred, a_affil, a_totp = make_actor(affil_manager_id, "affil-manager")

        db.add_all(
            [
                p_ident,
                p_cred,
                p_affil,
                f_ident,
                f_cred,
                f_affil,
                a_ident,
                a_cred,
                a_affil,
                # Explicit Phase 3D grants (Governance Precondition Fixture)
                ProviderTrustPermissionGrant(
                    id=uuid.uuid4(),
                    provider_id=prof_reviewer_id,
                    permission="PROFESSIONAL_REVIEW",
                    scope_type="GLOBAL",
                    facility_id=None,
                    granted_by_actor_id="synthetic-governance-officer",
                ),
                ProviderTrustPermissionGrant(
                    id=uuid.uuid4(),
                    provider_id=fac_reviewer_id,
                    permission="FACILITY_REVIEW",
                    scope_type="FACILITY",
                    facility_id=facility_a_id,
                    granted_by_actor_id="synthetic-governance-officer",
                ),
                ProviderTrustPermissionGrant(
                    id=uuid.uuid4(),
                    provider_id=affil_manager_id,
                    permission="AFFILIATION_MANAGE",
                    scope_type="FACILITY",
                    facility_id=facility_a_id,
                    granted_by_actor_id="synthetic-governance-officer",
                ),
            ]
        )
        await db.commit()

    return {
        "facility_a_id": facility_a_id,
        "facility_b_id": facility_b_id,
        "prof_reviewer_id": prof_reviewer_id,
        "fac_reviewer_id": fac_reviewer_id,
        "affil_manager_id": affil_manager_id,
        "prof_totp": p_totp,
        "fac_totp": f_totp,
        "affil_totp": a_totp,
        "prof_login": p_cred.login_identifier,
        "fac_login": f_cred.login_identifier,
        "affil_login": a_cred.login_identifier,
    }


# ==============================================================================
# SECTION 1: FRESH DATABASE AND MIGRATION VERIFICATION
# ==============================================================================


async def test_01_fresh_migration_and_zero_auto_grants(db_factory):
    """Verify fresh database has sole head and zero auto-created trust grants."""
    async with db_factory() as db:
        head_in_db = await db.scalar(
            text("SELECT version_num FROM public.alembic_version")
        )
        assert head_in_db == HEAD

        grants_count = await db.scalar(
            text("SELECT count(*) FROM public.provider_trust_permission_grant")
        )
        assert grants_count >= 0
        legacy_backfill_check = await db.scalar(
            text(
                "SELECT count(*) FROM public.provider_trust_permission_grant WHERE granted_by_actor_id = 'migration'"
            )
        )
        assert legacy_backfill_check == 0


# ==============================================================================
# SECTION 2: END-TO-END PROVIDER JOURNEY QUALIFICATION
# ==============================================================================


async def test_02_complete_provider_registration_and_trust_journey_e2e(
    qualification_client, db_factory, capture_transport
):
    """Qualify the complete provider registration, verification, MFA, review,

    affiliation activation, clinical eligibility gating, and fail-closed
    immediate invalidations as ONE connected security system.
    """
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    fac_a_id = fixture["facility_a_id"]

    # -------------------------------------------------------------------------
    # STEP 1 — PROVIDER REGISTRATION (Bootstrap with Zero Authority)
    # -------------------------------------------------------------------------
    provider_suffix = uuid.uuid4().hex[:8]
    p_email = f"candidate-{provider_suffix}@example.test"
    p_phone = "+919876543211"
    p_password = "CandidateStrongPass123!"

    reg_key = f"reg-key-{provider_suffix}"
    reg_payload = {
        "display_name": f"Dr. Candidate {provider_suffix}",
        "login_identifier": p_email,
        "contact_email": p_email,
        "contact_phone": p_phone,
        "password": p_password,
        "hospital_id": str(fac_a_id),
        "registration_authority_code": "MCI",
        "registration_number": f"MCI-{provider_suffix}",
    }

    reg_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={"Idempotency-Key": reg_key, "User-Agent": _USER_AGENT},
        json=reg_payload,
    )
    assert reg_resp.status_code == 201, reg_resp.text
    provider_a_id = uuid.UUID(reg_resp.json()["provider_id"])
    assert reg_resp.json()["registration_state"] == "registered"

    # Verify PostgreSQL state proves ZERO authority
    async with db_factory() as db:
        identity = await db.get(ProviderIdentity, provider_a_id)
        assert identity is not None
        assert identity.email_verified_at is None
        assert identity.phone_verified_at is None
        assert identity.is_active is True

        credential = await db.scalar(
            select(ProviderCredential).where(
                ProviderCredential.provider_id == provider_a_id
            )
        )
        assert credential is not None
        assert credential.mfa_enabled is False

        prof = await db.scalar(
            select(ProfessionalVerification).where(
                ProfessionalVerification.provider_id == provider_a_id
            )
        )
        assert prof is not None
        assert prof.status == ProfessionalVerificationStatus.NOT_SUBMITTED.value
        assert prof.version == 1

        affil = await db.scalar(
            select(ProviderHospitalAffiliation).where(
                ProviderHospitalAffiliation.provider_id == provider_a_id
            )
        )
        assert affil is not None
        assert affil.trust_status == AffiliationTrustStatus.PENDING_ACTIVATION.value
        assert affil.roles == []
        assert affil.version == 1

        # Check grants: ZERO trust grants for Provider A
        p_grants = await db.scalars(
            select(ProviderTrustPermissionGrant).where(
                ProviderTrustPermissionGrant.provider_id == provider_a_id
            )
        )
        assert len(p_grants.all()) == 0

    # -------------------------------------------------------------------------
    # STEP 2 — PRE-ASSURANCE DENIAL
    # -------------------------------------------------------------------------
    async with db_factory() as db:
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            identity,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=None,
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code
            is ClinicalEligibilityDenialCode.CONTACT_VERIFICATION_REQUIRED
        )

    # -------------------------------------------------------------------------
    # STEP 3 — PROVIDER SESSION (Login for Candidate Provider A)
    # -------------------------------------------------------------------------
    login_resp = await client.post(
        "/api/v2/auth/login",
        headers={
            "User-Agent": _USER_AGENT,
            "X-Forwarded-For": "127.0.0.1",
        },
        json={
            "login_identifier": p_email,
            "password": p_password,
            "hospital_id": str(fac_a_id),
        },
    )
    assert login_resp.status_code == 200, login_resp.text
    session_token_a = login_resp.json()["access_token"]
    client_ip_a = f"127.0.1.{int(provider_suffix[:4], 16) % 250 + 1}"
    auth_headers_a = {
        "Authorization": f"Bearer {session_token_a}",
        "User-Agent": _USER_AGENT,
        "X-Forwarded-For": client_ip_a,
    }

    # Prove User-Agent binding
    ua_mismatch_resp = await client.post(
        "/api/v2/auth/me/contact/email/challenge",
        headers={
            "Authorization": f"Bearer {session_token_a}",
            "User-Agent": "Wrong-Agent",
        },
    )
    assert ua_mismatch_resp.status_code == 401

    # -------------------------------------------------------------------------
    # STEP 4 — AUTHORITATIVE EMAIL VERIFICATION
    # -------------------------------------------------------------------------
    capture_transport.deliveries.clear()
    email_chal_resp = await client.post(
        "/api/v2/auth/me/contact/email/challenge",
        headers=auth_headers_a,
    )
    assert email_chal_resp.status_code == 202, email_chal_resp.text
    email_chal_id = uuid.UUID(email_chal_resp.json()["challenge_id"])

    # Challenge issuance alone must NOT verify email
    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        assert id_chk.email_verified_at is None

    assert len(capture_transport.deliveries) == 1
    _, _, email_verifier = capture_transport.deliveries[0]

    # Verify email
    email_verify_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={
            **auth_headers_a,
            "Idempotency-Key": f"email-verify-{provider_suffix}",
        },
        json={
            "challenge_id": str(email_chal_id),
            "verifier": email_verifier,
        },
    )
    assert email_verify_resp.status_code == 200, email_verify_resp.text

    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        assert id_chk.email_verified_at is not None

    # -------------------------------------------------------------------------
    # STEP 5 — AUTHORITATIVE PHONE VERIFICATION
    # -------------------------------------------------------------------------
    capture_transport.deliveries.clear()
    phone_chal_resp = await client.post(
        "/api/v2/auth/me/contact/phone/challenge",
        headers=auth_headers_a,
    )
    assert phone_chal_resp.status_code == 202
    phone_chal_id = uuid.UUID(phone_chal_resp.json()["challenge_id"])

    assert len(capture_transport.deliveries) == 1
    _, _, phone_verifier = capture_transport.deliveries[0]

    phone_verify_resp = await client.post(
        "/api/v2/auth/me/contact/phone/verify",
        headers={
            **auth_headers_a,
            "Idempotency-Key": f"phone-verify-{provider_suffix}",
        },
        json={
            "challenge_id": str(phone_chal_id),
            "verifier": phone_verifier,
        },
    )
    assert phone_verify_resp.status_code == 200, phone_verify_resp.text

    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        assert id_chk.phone_verified_at is not None

    # -------------------------------------------------------------------------
    # STEP 6 — MFA ENROLLMENT
    # -------------------------------------------------------------------------
    mfa_setup_resp = await client.post(
        "/api/v2/auth/mfa/setup",
        headers=auth_headers_a,
    )
    assert mfa_setup_resp.status_code == 200
    mfa_secret = mfa_setup_resp.json()["secret"]

    # Setup initiation alone must NOT enable MFA
    async with db_factory() as db:
        cred_chk = await db.scalar(
            select(ProviderCredential).where(
                ProviderCredential.provider_id == provider_a_id
            )
        )
        assert cred_chk.mfa_enabled is False

    # Complete MFA enrollment with synthetic TOTP
    totp_code = pyotp.TOTP(mfa_secret).now()
    mfa_verify_setup_resp = await client.post(
        "/api/v2/auth/mfa/setup/verify",
        headers=auth_headers_a,
        json={"totp_code": totp_code},
    )
    assert mfa_verify_setup_resp.status_code == 200

    # MFA is enabled and audit event staged
    async with db_factory() as db:
        cred_chk = await db.scalar(
            select(ProviderCredential).where(
                ProviderCredential.provider_id == provider_a_id
            )
        )
        assert cred_chk.mfa_enabled is True

        outbox_mfa = await db.scalar(
            text(
                "SELECT count(*) FROM public.audit_outbox WHERE event_type = 'PROVIDER_MFA_SETUP_SUCCESS' AND (actor_id = :target_id OR payload->>'target_id' = :target_id)"
            ),
            {"target_id": str(provider_a_id)},
        )
        assert outbox_mfa >= 1

    # -------------------------------------------------------------------------
    # STEP 7 — MFA-ASSURED PROVIDER LOGIN
    # -------------------------------------------------------------------------
    # Non-MFA assured session against trust routes must return 428
    unassured_trust_attempt = await client.post(
        "/api/v2/provider-trust/professional/me/submit",
        headers={
            **auth_headers_a,
            "Idempotency-Key": f"unassured-{provider_suffix}",
        },
        json={
            "expected_version": 1,
            "registration_authority_code": "MCI",
            "registration_number": f"MCI-{provider_suffix}",
        },
    )
    assert unassured_trust_attempt.status_code == 428
    assert unassured_trust_attempt.json()["detail"]["error_code"] == (
        "MFA_SESSION_ASSURANCE_REQUIRED"
    )

    # Now perform the full MFA login
    login_mfa_step1 = await client.post(
        "/api/v2/auth/login",
        headers={"User-Agent": _USER_AGENT, "X-Forwarded-For": "127.0.0.1"},
        json={
            "login_identifier": p_email,
            "password": p_password,
            "hospital_id": str(fac_a_id),
        },
    )
    assert login_mfa_step1.status_code == 200
    mfa_pending_token = login_mfa_step1.json()["mfa_token"]

    # Generate an unused adjacent TOTP counter without mutating Redis replay state
    totp = pyotp.TOTP(mfa_secret)
    totp_fresh = totp.at(datetime.now(timezone.utc) + timedelta(seconds=totp.interval))
    assert totp_fresh != totp_code

    login_mfa_step2 = await client.post(
        "/api/v2/auth/mfa/verify",
        headers={"User-Agent": _USER_AGENT, "X-Forwarded-For": "127.0.0.1"},
        json={
            "mfa_token": mfa_pending_token,
            "totp_code": totp_fresh,
            "hospital_id": str(fac_a_id),
        },
    )
    assert login_mfa_step2.status_code == 200, login_mfa_step2.text
    mfa_assured_token_a = login_mfa_step2.json()["access_token"]
    mfa_auth_headers_a = {
        "Authorization": f"Bearer {mfa_assured_token_a}",
        "User-Agent": _USER_AGENT,
    }

    # -------------------------------------------------------------------------
    # PROVE BOTH COUNTERS REMAIN CONSUMED & REPLAY IS REJECTED
    # -------------------------------------------------------------------------
    # 1. Read-only inspection of Redis replay markers: both counters must exist
    r_check = Redis.from_url(_get_redis_url(), decode_responses=True)
    replay_keys = [
        k async for k in r_check.scan_iter(match=f"*mfa_totp_used:{provider_a_id}:*")
    ]
    await r_check.close()
    assert (
        len(replay_keys) == 2
    ), f"Expected exactly 2 distinct durable replay markers in Redis, found {replay_keys}"

    # 2. Replaying the enrollment TOTP code through real auth fails closed
    replay_token_1 = await issue_mfa_pending_token(provider_a_id)
    replay_enroll_resp = await client.post(
        "/api/v2/auth/mfa/verify",
        headers={"User-Agent": _USER_AGENT, "X-Forwarded-For": "127.0.0.1"},
        json={
            "mfa_token": replay_token_1,
            "totp_code": totp_code,
            "hospital_id": str(fac_a_id),
        },
    )
    assert replay_enroll_resp.status_code == 401
    assert replay_enroll_resp.json()["detail"] == "Invalid authenticator code."

    # 3. Replaying the login TOTP code through real auth fails closed
    replay_token_2 = await issue_mfa_pending_token(provider_a_id)
    replay_login_resp = await client.post(
        "/api/v2/auth/mfa/verify",
        headers={"User-Agent": _USER_AGENT, "X-Forwarded-For": "127.0.0.1"},
        json={
            "mfa_token": replay_token_2,
            "totp_code": totp_fresh,
            "hospital_id": str(fac_a_id),
        },
    )
    assert replay_login_resp.status_code == 401
    assert replay_login_resp.json()["detail"] == "Invalid authenticator code."

    # 4. Direct service-level verification proves both timesteps remain locked
    assert not await verify_totp_code_once(
        provider_a_id, mfa_secret, totp_code, redis_client=get_async_redis_client()
    )
    assert not await verify_totp_code_once(
        provider_a_id, mfa_secret, totp_fresh, redis_client=get_async_redis_client()
    )

    # -------------------------------------------------------------------------
    # STEP 8 — PROFESSIONAL SELF SUBMISSION
    # -------------------------------------------------------------------------
    self_sub_resp = await client.post(
        "/api/v2/provider-trust/professional/me/submit",
        headers={
            **mfa_auth_headers_a,
            "Idempotency-Key": f"self-sub-{provider_suffix}",
        },
        json={
            "expected_version": 1,
            "registration_authority_code": "MCI",
            "registration_number": f"MCI-{provider_suffix}",
        },
    )
    assert self_sub_resp.status_code == 200, self_sub_resp.text
    assert self_sub_resp.json()["new_state"] == "PENDING_REVIEW"
    assert self_sub_resp.json()["version"] == 2

    # Verify no clinical capability gained merely from submission
    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code
            is ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_PENDING
        )

    # -------------------------------------------------------------------------
    # STEP 9 — PROFESSIONAL REVIEW
    # -------------------------------------------------------------------------
    prof_rev_session = await issue_provider_session_token(
        provider_id=fixture["prof_reviewer_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    prof_rev_headers = {
        "Authorization": f"Bearer {prof_rev_session}",
        "User-Agent": _USER_AGENT,
    }

    prof_verify_resp = await client.post(
        f"/api/v2/provider-trust/professional/{provider_a_id}/verify",
        headers={
            **prof_rev_headers,
            "Idempotency-Key": f"prof-verify-{provider_suffix}",
        },
        json={
            "expected_version": 2,
            "registration_authority_code": "MCI",
            "registration_number_normalized": f"MCI-NORM-{provider_suffix}",
            "verification_method": "SYNTHETIC_REGISTRY_QUERY",
            "verification_source": "SYNTHETIC_COUNCIL",
            "verification_reference": f"REF-{provider_suffix}",
            "identity_binding_method": "SYNTHETIC_MATCH",
            "identity_binding_status": "MATCHED",
        },
    )
    assert prof_verify_resp.status_code == 200, prof_verify_resp.text
    assert prof_verify_resp.json()["new_state"] == "VERIFIED"
    assert prof_verify_resp.json()["version"] == 3

    async with db_factory() as db:
        prof_row = await db.scalar(
            select(ProfessionalVerification).where(
                ProfessionalVerification.provider_id == provider_a_id
            )
        )
        assert prof_row.status == "VERIFIED"
        assert str(prof_row.reviewer_id) == str(fixture["prof_reviewer_id"])
        assert prof_row.version == 3

    # -------------------------------------------------------------------------
    # STEP 10 — FACILITY VERIFICATION
    # -------------------------------------------------------------------------
    fac_rev_session = await issue_provider_session_token(
        provider_id=fixture["fac_reviewer_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    fac_rev_headers = {
        "Authorization": f"Bearer {fac_rev_session}",
        "User-Agent": _USER_AGENT,
    }

    fac_sub_resp = await client.post(
        f"/api/v2/provider-trust/facilities/{fac_a_id}/submit",
        headers={
            **fac_rev_headers,
            "Idempotency-Key": f"fac-sub-{provider_suffix}",
        },
        json={"expected_version": 1},
    )
    assert fac_sub_resp.status_code == 200, fac_sub_resp.text
    assert fac_sub_resp.json()["new_state"] == "PENDING_VERIFICATION"

    fac_verify_resp = await client.post(
        f"/api/v2/provider-trust/facilities/{fac_a_id}/verify",
        headers={
            **fac_rev_headers,
            "Idempotency-Key": f"fac-verify-{provider_suffix}",
        },
        json={
            "expected_version": 2,
            "verification_method": "SYNTHETIC_REGISTRATION",
            "verification_source": "SYNTHETIC_STATE_PORTAL",
            "verification_reference": f"HOSP-REF-{provider_suffix}",
        },
    )
    assert fac_verify_resp.status_code == 200, fac_verify_resp.text
    assert fac_verify_resp.json()["new_state"] == "VERIFIED"

    # -------------------------------------------------------------------------
    # STEP 11 — AFFILIATION ACTIVATION
    # -------------------------------------------------------------------------
    affil_manager_session = await issue_provider_session_token(
        provider_id=fixture["affil_manager_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    affil_mgr_headers = {
        "Authorization": f"Bearer {affil_manager_session}",
        "User-Agent": _USER_AGENT,
    }

    async with db_factory() as db:
        affil_row = await db.scalar(
            select(ProviderHospitalAffiliation).where(
                ProviderHospitalAffiliation.provider_id == provider_a_id
            )
        )
        affil_id = affil_row.id

    affil_act_resp = await client.post(
        f"/api/v2/provider-trust/affiliations/{affil_id}/activate",
        headers={
            **affil_mgr_headers,
            "Idempotency-Key": f"affil-act-{provider_suffix}",
        },
        json={"expected_version": 1},
    )
    assert affil_act_resp.status_code == 200, affil_act_resp.text
    assert affil_act_resp.json()["new_state"] == "ACTIVE"

    # -------------------------------------------------------------------------
    # CRITICAL AUTHORITY CHECKPOINT: Fully trusted but roleless remains denied!
    # -------------------------------------------------------------------------
    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        affil_chk = await db.get(ProviderHospitalAffiliation, affil_id)
        assert affil_chk.trust_status == "ACTIVE"
        assert affil_chk.roles == []  # roleless

        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code
            is ClinicalEligibilityDenialCode.CLINICAL_CAPABILITY_NOT_GRANTED
        )

    # -------------------------------------------------------------------------
    # CLINICAL CAPABILITY PRECONDITION: Set synthetic affiliation role
    # -------------------------------------------------------------------------
    async with db_factory() as db:
        affil_chk = await db.get(ProviderHospitalAffiliation, affil_id)
        affil_chk.roles = ["clinician"]
        await db.commit()

    # Verify clinical eligibility succeeds
    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is True
        assert clin_check.denial_code is None

    # -------------------------------------------------------------------------
    # IMMEDIATE FAIL-CLOSED QUALIFICATIONS
    # -------------------------------------------------------------------------
    # 1. Professional Suspension
    susp_resp = await client.post(
        f"/api/v2/provider-trust/professional/{provider_a_id}/suspend",
        headers={
            **prof_rev_headers,
            "Idempotency-Key": f"prof-susp-{provider_suffix}",
        },
        json={
            "expected_version": 3,
            "decision_reason_code": "SYNTHETIC_SUSPENSION",
        },
    )
    assert susp_resp.status_code == 200

    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code
            is ClinicalEligibilityDenialCode.PROFESSIONAL_SUSPENDED
        )

    # Restore Professional
    rest_resp = await client.post(
        f"/api/v2/provider-trust/professional/{provider_a_id}/restore",
        headers={
            **prof_rev_headers,
            "Idempotency-Key": f"prof-rest-{provider_suffix}",
        },
        json={
            "expected_version": 4,
            "registration_authority_code": "MCI",
            "registration_number_normalized": f"MCI-NORM-{provider_suffix}",
            "verification_method": "SYNTHETIC_REGISTRY_QUERY",
            "verification_source": "SYNTHETIC_COUNCIL",
            "verification_reference": f"REF-REST-{provider_suffix}",
            "identity_binding_method": "SYNTHETIC_MATCH",
            "identity_binding_status": "MATCHED",
        },
    )
    assert rest_resp.status_code == 200

    # 2. Facility Suspension
    fac_susp_resp = await client.post(
        f"/api/v2/provider-trust/facilities/{fac_a_id}/suspend",
        headers={
            **fac_rev_headers,
            "Idempotency-Key": f"fac-susp-{provider_suffix}",
        },
        json={
            "expected_version": 3,
            "decision_reason_code": "SYNTHETIC_FACILITY_SUSPENSION",
        },
    )
    assert fac_susp_resp.status_code == 200

    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code is ClinicalEligibilityDenialCode.FACILITY_SUSPENDED
        )

    # Restore Facility
    fac_rest_resp = await client.post(
        f"/api/v2/provider-trust/facilities/{fac_a_id}/restore",
        headers={
            **fac_rev_headers,
            "Idempotency-Key": f"fac-rest-{provider_suffix}",
        },
        json={
            "expected_version": 4,
            "verification_method": "SYNTHETIC_REGISTRATION",
            "verification_source": "SYNTHETIC_STATE_PORTAL",
            "verification_reference": f"HOSP-REST-{provider_suffix}",
        },
    )
    assert fac_rest_resp.status_code == 200

    # 3. Affiliation Suspension
    affil_susp_resp = await client.post(
        f"/api/v2/provider-trust/affiliations/{affil_id}/suspend",
        headers={
            **affil_mgr_headers,
            "Idempotency-Key": f"affil-susp-{provider_suffix}",
        },
        json={
            "expected_version": 2,
            "decision_reason_code": "SYNTHETIC_AFFILIATION_SUSPENSION",
        },
    )
    assert affil_susp_resp.status_code == 200

    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code
            is ClinicalEligibilityDenialCode.AFFILIATION_SUSPENDED
        )

    # Restore Affiliation
    affil_rest_resp = await client.post(
        f"/api/v2/provider-trust/affiliations/{affil_id}/restore",
        headers={
            **affil_mgr_headers,
            "Idempotency-Key": f"affil-rest-{provider_suffix}",
        },
        json={"expected_version": 3},
    )
    assert affil_rest_resp.status_code == 200

    # Verify clinical eligibility allowed again
    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is True

    # -------------------------------------------------------------------------
    # CONTACT REVOCATION EFFECT
    # -------------------------------------------------------------------------
    mut_resp = await client.put(
        "/api/v2/auth/me/contact/email",
        headers={
            **mfa_auth_headers_a,
            "Idempotency-Key": f"email-mut-{provider_suffix}",
        },
        json={"contact": f"mutated-{provider_suffix}@example.test"},
    )
    assert mut_resp.status_code == 200

    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, provider_a_id)
        assert id_chk.email_verified_at is None

        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            id_chk,
            InteractiveClinicalAuthentication(
                provider_id=provider_a_id,
                hospital_id=fac_a_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code
            is ClinicalEligibilityDenialCode.CONTACT_VERIFICATION_REQUIRED
        )

    # -------------------------------------------------------------------------
    # GRANT REVOCATION EFFECT (Immediate, No Re-login Required)
    # -------------------------------------------------------------------------
    async with db_factory() as db:
        grant = await db.scalar(
            select(ProviderTrustPermissionGrant).where(
                ProviderTrustPermissionGrant.provider_id == fixture["prof_reviewer_id"]
            )
        )
        grant.revoked_at = datetime.now(timezone.utc)
        await db.commit()

    revoked_grant_resp = await client.post(
        f"/api/v2/provider-trust/professional/{provider_a_id}/suspend",
        headers={
            **prof_rev_headers,
            "Idempotency-Key": f"post-revoke-{provider_suffix}",
        },
        json={
            "expected_version": 5,
            "decision_reason_code": "POST_REVOKE_ATTEMPT",
        },
    )
    assert revoked_grant_resp.status_code == 403
    assert revoked_grant_resp.json() == {"error_code": "AUTHORIZATION_DENIED"}

    # -------------------------------------------------------------------------
    # ACCOUNT / CREDENTIAL DEACTIVATION EFFECT
    # -------------------------------------------------------------------------
    async with db_factory() as db:
        fac_cred = await db.scalar(
            select(ProviderCredential).where(
                ProviderCredential.provider_id == fixture["fac_reviewer_id"]
            )
        )
        fac_cred.is_active = False
        await db.commit()

    deactivated_cred_resp = await client.post(
        f"/api/v2/provider-trust/facilities/{fac_a_id}/suspend",
        headers={
            **fac_rev_headers,
            "Idempotency-Key": f"post-deact-{provider_suffix}",
        },
        json={
            "expected_version": 5,
            "decision_reason_code": "DEACT_ATTEMPT",
        },
    )
    assert deactivated_cred_resp.status_code == 403
    assert deactivated_cred_resp.json() == {"error_code": "AUTHORIZATION_DENIED"}


# ==============================================================================
# SECTION 3: ADVERSARIAL ATTACK PROOFS & HARDENING
# ==============================================================================


async def test_03_registration_adversarial_and_idempotency(
    qualification_client, db_factory
):
    """Prove authority-bearing fields are rejected and idempotency operates reliably."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    fac_a_id = fixture["facility_a_id"]
    suffix = uuid.uuid4().hex[:8]

    base_payload = {
        "display_name": f"Dr. Probe {suffix}",
        "login_identifier": f"probe-{suffix}@example.test",
        "contact_email": f"probe-{suffix}@example.test",
        "contact_phone": "+919876543212",
        "password": "ProbeStrongPass123!",
        "hospital_id": str(fac_a_id),
    }

    forbidden_fields = [
        ("is_active", True),
        ("email_verified_at", datetime.now(timezone.utc).isoformat()),
        ("phone_verified_at", datetime.now(timezone.utc).isoformat()),
        ("mfa_enabled", True),
        ("role", "admin"),
        ("roles", ["clinician"]),
        ("trust_status", "ACTIVE"),
        ("status", "VERIFIED"),
        ("reviewer_id", str(uuid.uuid4())),
        ("capabilities", ["CLINICAL_DISCOVERY_READ"]),
        ("permission", "PROFESSIONAL_REVIEW"),
    ]

    for key, val in forbidden_fields:
        bad_payload = {**base_payload, key: val}
        resp = await client.post(
            "/api/v2/auth/provider/register",
            headers={
                "Idempotency-Key": f"probe-{key}-{suffix}",
                "User-Agent": _USER_AGENT,
            },
            json=bad_payload,
        )
        assert resp.status_code == 422, f"Expected 422 for field '{key}'"

    # Registration Idempotency: replay same request
    key = f"idemp-{suffix}"
    first_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={"Idempotency-Key": key, "User-Agent": _USER_AGENT},
        json=base_payload,
    )
    assert first_resp.status_code == 201
    provider_id = first_resp.json()["provider_id"]

    replay_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={"Idempotency-Key": key, "User-Agent": _USER_AGENT},
        json=base_payload,
    )
    assert replay_resp.status_code == 201
    assert replay_resp.json()["idempotent_replay"] is True
    assert replay_resp.json()["provider_id"] == provider_id

    # Same key + conflicting payload -> 409
    conflict_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={"Idempotency-Key": key, "User-Agent": _USER_AGENT},
        json={**base_payload, "display_name": "Different Name"},
    )
    assert conflict_resp.status_code == 409


async def test_04_contact_assurance_adversarial(
    qualification_client, db_factory, capture_transport, monkeypatch
):
    """Prove wrong verifier, expired challenge, and reused challenge fail closed."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    suffix = uuid.uuid4().hex[:8]

    reg_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={
            "Idempotency-Key": f"reg-{suffix}",
            "User-Agent": _USER_AGENT,
        },
        json={
            "display_name": f"Dr. Contact Probe {suffix}",
            "login_identifier": f"cprobe-{suffix}@example.test",
            "contact_email": f"cprobe-{suffix}@example.test",
            "contact_phone": "+919876543213",
            "password": "Password123!Safe",
            "hospital_id": str(fixture["facility_a_id"]),
        },
    )
    assert reg_resp.status_code == 201
    p_id = uuid.UUID(reg_resp.json()["provider_id"])

    token = await issue_provider_session_token(
        provider_id=p_id, user_agent=_USER_AGENT, client_ip="127.0.0.1"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
        "X-Forwarded-For": f"198.51.100.{int(suffix[:4], 16) % 200 + 1}",
    }

    capture_transport.deliveries.clear()
    chal_resp = await client.post(
        "/api/v2/auth/me/contact/email/challenge", headers=headers
    )
    assert chal_resp.status_code == 202
    chal_id = chal_resp.json()["challenge_id"]
    _, _, verifier = capture_transport.deliveries[0]

    # Challenge issuance alone must NOT set verified_at
    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, p_id)
        assert id_chk.email_verified_at is None

    # 1. Malformed verifier (< 32 chars) -> 422
    short_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={**headers, "Idempotency-Key": f"v-short-{suffix}"},
        json={"challenge_id": chal_id, "verifier": "too-short"},
    )
    assert short_resp.status_code == 422

    # 2. Wrong verifier (valid length, invalid value) -> 422 CONTACT_CHALLENGE_VERIFICATION_FAILED
    wrong_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={**headers, "Idempotency-Key": f"v-wrong-{suffix}"},
        json={"challenge_id": chal_id, "verifier": "wrong-verifier-" + "x" * 32},
    )
    assert wrong_resp.status_code == 422
    assert (
        wrong_resp.json()["detail"]["error_code"]
        == "CONTACT_CHALLENGE_VERIFICATION_FAILED"
    )

    # 3. Expired challenge -> 422 CONTACT_CHALLENGE_VERIFICATION_FAILED
    async with db_factory() as db:
        c_row = await db.get(ProviderContactVerificationChallenge, uuid.UUID(chal_id))
        c_row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()

    exp_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={**headers, "Idempotency-Key": f"v-exp-{suffix}"},
        json={"challenge_id": chal_id, "verifier": verifier},
    )
    assert exp_resp.status_code == 422
    assert (
        exp_resp.json()["detail"]["error_code"]
        == "CONTACT_CHALLENGE_VERIFICATION_FAILED"
    )

    # 4. Valid verifier on fresh challenge -> 200
    capture_transport.deliveries.clear()
    live_chal_resp = await client.post(
        "/api/v2/auth/me/contact/email/challenge", headers=headers
    )
    assert live_chal_resp.status_code == 202
    live_chal_id = live_chal_resp.json()["challenge_id"]
    _, _, live_verifier = capture_transport.deliveries[0]

    good_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={**headers, "Idempotency-Key": f"v-good-{suffix}"},
        json={"challenge_id": live_chal_id, "verifier": live_verifier},
    )
    assert good_resp.status_code == 200
    assert good_resp.json()["verified"] is True

    async with db_factory() as db:
        id_chk = await db.get(ProviderIdentity, p_id)
        assert id_chk.email_verified_at is not None

    # 5. Reused challenge with different idempotency key -> 422 CONTACT_CHALLENGE_VERIFICATION_FAILED
    reuse_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={**headers, "Idempotency-Key": f"v-reuse-{suffix}"},
        json={"challenge_id": live_chal_id, "verifier": live_verifier},
    )
    assert reuse_resp.status_code == 422
    assert (
        reuse_resp.json()["detail"]["error_code"]
        == "CONTACT_CHALLENGE_VERIFICATION_FAILED"
    )

    # 6. Old-contact challenge after contact replacement -> 422 CONTACT_CHALLENGE_VERIFICATION_FAILED
    # Register secondary provider to isolate challenge rate limit budget
    suffix_b = uuid.uuid4().hex[:8]
    reg_b_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={
            "Idempotency-Key": f"reg-b-{suffix_b}",
            "User-Agent": _USER_AGENT,
        },
        json={
            "display_name": f"Dr. Contact Probe B {suffix_b}",
            "login_identifier": f"cprobe-b-{suffix_b}@example.test",
            "contact_email": f"cprobe-b-{suffix_b}@example.test",
            "contact_phone": "+919876543299",
            "password": "Password123!Safe",
            "hospital_id": str(fixture["facility_a_id"]),
        },
    )
    assert reg_b_resp.status_code == 201
    p_id_b = uuid.UUID(reg_b_resp.json()["provider_id"])

    token_b = await issue_provider_session_token(
        provider_id=p_id_b, user_agent=_USER_AGENT, client_ip="127.0.0.1"
    )
    headers_b = {
        "Authorization": f"Bearer {token_b}",
        "User-Agent": _USER_AGENT,
        "X-Forwarded-For": f"198.51.101.{int(suffix_b[:4], 16) % 200 + 1}",
    }

    capture_transport.deliveries.clear()
    pre_replace_chal = await client.post(
        "/api/v2/auth/me/contact/email/challenge", headers=headers_b
    )
    assert pre_replace_chal.status_code == 202
    pre_rep_id = pre_replace_chal.json()["challenge_id"]
    _, _, pre_rep_verifier = capture_transport.deliveries[0]

    put_resp = await client.put(
        "/api/v2/auth/me/contact/email",
        headers={**headers_b, "Idempotency-Key": f"put-{suffix_b}"},
        json={"contact": f"replaced-{suffix_b}@example.test"},
    )
    assert put_resp.status_code == 200

    old_verify_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={**headers_b, "Idempotency-Key": f"v-old-{suffix_b}"},
        json={"challenge_id": pre_rep_id, "verifier": pre_rep_verifier},
    )
    assert old_verify_resp.status_code == 422
    assert (
        old_verify_resp.json()["detail"]["error_code"]
        == "CONTACT_CHALLENGE_VERIFICATION_FAILED"
    )

    # 7. Session Separation Regression: Prove Auth Session != Mutation Session and in_transaction() is False
    suffix_c = uuid.uuid4().hex[:8]
    reg_c_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={
            "Idempotency-Key": f"reg-c-{suffix_c}",
            "User-Agent": _USER_AGENT,
        },
        json={
            "display_name": f"Dr. Session Proof {suffix_c}",
            "login_identifier": f"cprobe-c-{suffix_c}@example.test",
            "contact_email": f"cprobe-c-{suffix_c}@example.test",
            "contact_phone": "+919876543298",
            "password": "Password123!Safe",
            "hospital_id": str(fixture["facility_a_id"]),
        },
    )
    assert reg_c_resp.status_code == 201
    p_id_c = uuid.UUID(reg_c_resp.json()["provider_id"])

    token_c = await issue_provider_session_token(
        provider_id=p_id_c, user_agent=_USER_AGENT, client_ip="127.0.0.1"
    )
    headers_c = {
        "Authorization": f"Bearer {token_c}",
        "User-Agent": _USER_AGENT,
        "X-Forwarded-For": f"198.51.102.{int(suffix_c[:4], 16) % 200 + 1}",
    }

    auth_sessions: list[Any] = []
    mutation_sessions: list[Any] = []

    async def custom_auth_db():
        async with db_factory() as session:
            auth_sessions.append(session)
            yield session

    async def custom_mut_db():
        async with db_factory() as session:
            mutation_sessions.append(session)
            assert session.in_transaction() is False
            yield session

    app.dependency_overrides[get_db_session] = custom_auth_db
    app.dependency_overrides[get_provider_contact_mutation_session] = custom_mut_db

    capture_transport.deliveries.clear()
    try:
        chal_c_resp = await client.post(
            "/api/v2/auth/me/contact/email/challenge", headers=headers_c
        )
        assert chal_c_resp.status_code == 202
        assert len(auth_sessions) > 0
        assert len(mutation_sessions) > 0
        auth_session = auth_sessions[-1]
        mutation_session = mutation_sessions[-1]
        assert (
            auth_session is not mutation_session
        ), "CRITICAL: Auth DB session and Mutation DB session MUST be distinct instances"
        assert (
            id(auth_session) != id(mutation_session)
        ), "CRITICAL: Auth DB session and Mutation DB session MUST be distinct instances"
    finally:

        async def override_db():
            async with db_factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        app.dependency_overrides[get_provider_contact_mutation_session] = override_db

    # 8. Audit Failure Atomicity: If audit outbox staging fails, verify complete rollback
    chal_c_id = chal_c_resp.json()["challenge_id"]
    _, _, chal_c_verifier = capture_transport.deliveries[0]

    with patch(
        "app.services.provider_contact_assurance_service.enqueue_audit_event",
        side_effect=RuntimeError("Simulated audit staging failure"),
    ):
        atomic_fail_resp = await client.post(
            "/api/v2/auth/me/contact/email/verify",
            headers={**headers_c, "Idempotency-Key": f"v-atom-{suffix_c}"},
            json={"challenge_id": chal_c_id, "verifier": chal_c_verifier},
        )
        assert atomic_fail_resp.status_code in {500, 503}

    # Verify rollback: Provider email_verified_at is still None, challenge not consumed or succeeded
    async with db_factory() as db:
        prov_chk = await db.get(ProviderIdentity, p_id_c)
        assert prov_chk.email_verified_at is None

        chal_chk = await db.get(
            ProviderContactVerificationChallenge, uuid.UUID(chal_c_id)
        )
        assert chal_chk.consumed_at is None
        assert chal_chk.succeeded_at is None

        # Idempotency must not have recorded completed success
        idemp_row = await db.scalar(
            text(
                "SELECT response_status FROM public.mutation_idempotency WHERE idempotency_key = :key"
            ),
            {"key": f"v-atom-{suffix_c}"},
        )
        assert idemp_row is None

    # Now verify cleanly without audit failure -> succeeds
    atomic_success_resp = await client.post(
        "/api/v2/auth/me/contact/email/verify",
        headers={**headers_c, "Idempotency-Key": f"v-atom-clean-{suffix_c}"},
        json={"challenge_id": chal_c_id, "verifier": chal_c_verifier},
    )
    assert atomic_success_resp.status_code == 200

    async with db_factory() as db:
        prov_chk = await db.get(ProviderIdentity, p_id_c)
        assert prov_chk.email_verified_at is not None
        chal_chk = await db.get(
            ProviderContactVerificationChallenge, uuid.UUID(chal_c_id)
        )
        assert chal_chk.succeeded_at is not None


async def test_05_professional_adversarial_proofs(qualification_client, db_factory):
    """Prove self-review, forged reviewer_id, and stale version are rejected."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    suffix = uuid.uuid4().hex[:8]

    reg_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={
            "Idempotency-Key": f"reg-adv-{suffix}",
            "User-Agent": _USER_AGENT,
        },
        json={
            "display_name": f"Dr. Adv Probe {suffix}",
            "login_identifier": f"advprobe-{suffix}@example.test",
            "contact_email": f"advprobe-{suffix}@example.test",
            "contact_phone": "+919876543214",
            "password": "Password123!Safe",
            "hospital_id": str(fixture["facility_a_id"]),
        },
    )
    p_id = uuid.UUID(reg_resp.json()["provider_id"])

    cand_session = await issue_provider_session_token(
        provider_id=p_id,
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    cand_headers = {
        "Authorization": f"Bearer {cand_session}",
        "User-Agent": _USER_AGENT,
    }

    await client.post(
        "/api/v2/provider-trust/professional/me/submit",
        headers={**cand_headers, "Idempotency-Key": f"sub-{suffix}"},
        json={
            "expected_version": 1,
            "registration_authority_code": "MCI",
            "registration_number": f"REG-{suffix}",
        },
    )

    # 1. Self-review prohibition
    self_rev_resp = await client.post(
        f"/api/v2/provider-trust/professional/{p_id}/verify",
        headers={**cand_headers, "Idempotency-Key": f"self-rev-{suffix}"},
        json={
            "expected_version": 2,
            "registration_authority_code": "MCI",
            "registration_number_normalized": f"REG-{suffix}",
            "verification_method": "QUERY",
            "verification_source": "COUNCIL",
            "verification_reference": "REF",
            "identity_binding_method": "MATCH",
            "identity_binding_status": "MATCHED",
        },
    )
    assert self_rev_resp.status_code == 403
    assert self_rev_resp.json() == {"error_code": "AUTHORIZATION_DENIED"}

    # 2. Forged reviewer_id in request body
    rev_session = await issue_provider_session_token(
        provider_id=fixture["prof_reviewer_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    rev_headers = {
        "Authorization": f"Bearer {rev_session}",
        "User-Agent": _USER_AGENT,
    }

    forged_body_resp = await client.post(
        f"/api/v2/provider-trust/professional/{p_id}/verify",
        headers={**rev_headers, "Idempotency-Key": f"forged-{suffix}"},
        json={
            "expected_version": 2,
            "registration_authority_code": "MCI",
            "registration_number_normalized": f"REG-{suffix}",
            "verification_method": "QUERY",
            "verification_source": "COUNCIL",
            "verification_reference": "REF",
            "identity_binding_method": "MATCH",
            "identity_binding_status": "MATCHED",
            "reviewer_id": str(uuid.uuid4()),
        },
    )
    assert forged_body_resp.status_code == 422

    # 3. Stale version conflict: expected_version = 999
    stale_resp = await client.post(
        f"/api/v2/provider-trust/professional/{p_id}/verify",
        headers={**rev_headers, "Idempotency-Key": f"stale-{suffix}"},
        json={
            "expected_version": 999,
            "registration_authority_code": "MCI",
            "registration_number_normalized": f"REG-{suffix}",
            "verification_method": "QUERY",
            "verification_source": "COUNCIL",
            "verification_reference": "REF",
            "identity_binding_method": "MATCH",
            "identity_binding_status": "MATCHED",
        },
    )
    assert stale_resp.status_code == 409
    assert stale_resp.json() == {"error_code": "LIFECYCLE_VERSION_CONFLICT"}


async def test_06_facility_and_affiliation_scoping_adversarial(
    qualification_client, db_factory
):
    """Prove cross-facility review and self affiliation management are strictly denied."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    fac_b_id = fixture["facility_b_id"]
    suffix = uuid.uuid4().hex[:8]

    fac_rev_session = await issue_provider_session_token(
        provider_id=fixture["fac_reviewer_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    fac_rev_headers = {
        "Authorization": f"Bearer {fac_rev_session}",
        "User-Agent": _USER_AGENT,
    }

    cross_fac_resp = await client.post(
        f"/api/v2/provider-trust/facilities/{fac_b_id}/submit",
        headers={
            **fac_rev_headers,
            "Idempotency-Key": f"cross-fac-{suffix}",
            "X-Hospital-Id": str(fixture["facility_a_id"]),
        },
        json={"expected_version": 1},
    )
    assert cross_fac_resp.status_code == 403
    assert cross_fac_resp.json() == {"error_code": "AUTHORIZATION_DENIED"}

    affil_mgr_session = await issue_provider_session_token(
        provider_id=fixture["affil_manager_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    affil_mgr_headers = {
        "Authorization": f"Bearer {affil_mgr_session}",
        "User-Agent": _USER_AGENT,
    }

    async with db_factory() as db:
        own_affil = await db.scalar(
            select(ProviderHospitalAffiliation).where(
                ProviderHospitalAffiliation.provider_id == fixture["affil_manager_id"]
            )
        )

    own_mgmt_resp = await client.post(
        f"/api/v2/provider-trust/affiliations/{own_affil.id}/suspend",
        headers={**affil_mgr_headers, "Idempotency-Key": f"own-affil-{suffix}"},
        json={
            "expected_version": 1,
            "decision_reason_code": "SELF_SUSPEND_TEST",
        },
    )
    assert own_mgmt_resp.status_code == 403
    assert own_mgmt_resp.json() == {"error_code": "AUTHORIZATION_DENIED"}


async def test_07_mark_recheck_due_no_grace_proof(qualification_client, db_factory):
    """Prove HTTP reviewer paths cannot manufacture grace or inject source failure reasons."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    suffix = uuid.uuid4().hex[:8]

    reg_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={
            "Idempotency-Key": f"reg-chk-{suffix}",
            "User-Agent": _USER_AGENT,
        },
        json={
            "display_name": f"Dr. Recheck {suffix}",
            "login_identifier": f"recheck-{suffix}@example.test",
            "contact_email": f"recheck-{suffix}@example.test",
            "contact_phone": "+919876543215",
            "password": "Password123!Safe",
            "hospital_id": str(fixture["facility_a_id"]),
        },
    )
    p_id = uuid.UUID(reg_resp.json()["provider_id"])

    rev_session = await issue_provider_session_token(
        provider_id=fixture["prof_reviewer_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    rev_headers = {
        "Authorization": f"Bearer {rev_session}",
        "User-Agent": _USER_AGENT,
    }

    async with db_factory() as db:
        c_ident = await db.get(ProviderIdentity, p_id)
        c_ident.email_verified_at = datetime.now(timezone.utc)
        c_ident.phone_verified_at = datetime.now(timezone.utc)
        c_cred = await db.scalar(
            select(ProviderCredential).where(ProviderCredential.provider_id == p_id)
        )
        c_cred.mfa_enabled = True
        await db.commit()

    cand_session = await issue_provider_session_token(
        provider_id=p_id,
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    sub_resp = await client.post(
        "/api/v2/provider-trust/professional/me/submit",
        headers={
            "Authorization": f"Bearer {cand_session}",
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": f"sub-chk-{suffix}",
        },
        json={
            "expected_version": 1,
            "registration_authority_code": "MCI",
            "registration_number": f"REG-CHK-{suffix}",
        },
    )
    assert sub_resp.status_code == 200, sub_resp.text

    v_resp = await client.post(
        f"/api/v2/provider-trust/professional/{p_id}/verify",
        headers={**rev_headers, "Idempotency-Key": f"v-chk-{suffix}"},
        json={
            "expected_version": 2,
            "registration_authority_code": "MCI",
            "registration_number_normalized": f"REG-CHK-{suffix}",
            "verification_method": "Q",
            "verification_source": "S",
            "verification_reference": "R",
            "identity_binding_method": "M",
            "identity_binding_status": "MATCHED",
        },
    )
    assert v_resp.status_code == 200, v_resp.text

    # Injected grace fields rejected with 422
    forged_recheck = await client.post(
        f"/api/v2/provider-trust/professional/{p_id}/mark-recheck-due",
        headers={**rev_headers, "Idempotency-Key": f"forged-chk-{suffix}"},
        json={
            "expected_version": 3,
            "grace_expires_at": datetime.now(timezone.utc).isoformat(),
            "recheck_failure_reason": "SOURCE_UNAVAILABLE",
        },
    )
    assert forged_recheck.status_code == 422

    # Legitimate call
    recheck_resp = await client.post(
        f"/api/v2/provider-trust/professional/{p_id}/mark-recheck-due",
        headers={**rev_headers, "Idempotency-Key": f"legit-chk-{suffix}"},
        json={"expected_version": 3},
    )
    assert recheck_resp.status_code == 200, recheck_resp.text

    async with db_factory() as db:
        prof = await db.scalar(
            select(ProfessionalVerification).where(
                ProfessionalVerification.provider_id == p_id
            )
        )
        assert prof.status == "RECHECK_DUE"
        assert prof.recheck_failure_reason is None
        assert prof.grace_expires_at is None
        assert prof.recheck_attempted_at is not None

        # Clinical denial when RECHECK_DUE has no permitted grace
        ident = await db.get(ProviderIdentity, p_id)
        clin_check = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_interactive(
            db,
            ident,
            InteractiveClinicalAuthentication(
                provider_id=p_id,
                hospital_id=fixture["facility_a_id"],
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=datetime.now(timezone.utc),
            ),
            ClinicalCapability.RECORD_READ,
        )
        assert clin_check.allowed is False
        assert (
            clin_check.denial_code
            is ClinicalEligibilityDenialCode.PROFESSIONAL_RECHECK_NOT_ELIGIBLE
        )


async def test_08_contradictory_http_reviewer_race(qualification_client, db_factory):
    """Run concurrent HTTP decision race: exactly one 200, exactly one 409."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    suffix = uuid.uuid4().hex[:8]

    reg_resp = await client.post(
        "/api/v2/auth/provider/register",
        headers={
            "Idempotency-Key": f"reg-race-{suffix}",
            "User-Agent": _USER_AGENT,
        },
        json={
            "display_name": f"Dr. Race {suffix}",
            "login_identifier": f"race-{suffix}@example.test",
            "contact_email": f"race-{suffix}@example.test",
            "contact_phone": "+919876543216",
            "password": "Password123!Safe",
            "hospital_id": str(fixture["facility_a_id"]),
        },
    )
    p_id = uuid.UUID(reg_resp.json()["provider_id"])

    async with db_factory() as db:
        c_ident = await db.get(ProviderIdentity, p_id)
        c_ident.email_verified_at = datetime.now(timezone.utc)
        c_ident.phone_verified_at = datetime.now(timezone.utc)
        c_cred = await db.scalar(
            select(ProviderCredential).where(ProviderCredential.provider_id == p_id)
        )
        c_cred.mfa_enabled = True
        await db.commit()

    cand_session = await issue_provider_session_token(
        provider_id=p_id,
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    sub_resp = await client.post(
        "/api/v2/provider-trust/professional/me/submit",
        headers={
            "Authorization": f"Bearer {cand_session}",
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": f"sub-race-{suffix}",
        },
        json={
            "expected_version": 1,
            "registration_authority_code": "MCI",
            "registration_number": f"REG-RACE-{suffix}",
        },
    )
    assert sub_resp.status_code == 200, sub_resp.text

    rev_session = await issue_provider_session_token(
        provider_id=fixture["prof_reviewer_id"],
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )
    rev_headers = {
        "Authorization": f"Bearer {rev_session}",
        "User-Agent": _USER_AGENT,
    }

    async def task_verify():
        return await client.post(
            f"/api/v2/provider-trust/professional/{p_id}/verify",
            headers={**rev_headers, "Idempotency-Key": f"race-v-{suffix}"},
            json={
                "expected_version": 2,
                "registration_authority_code": "MCI",
                "registration_number_normalized": f"REG-RACE-{suffix}",
                "verification_method": "Q",
                "verification_source": "S",
                "verification_reference": "R",
                "identity_binding_method": "M",
                "identity_binding_status": "MATCHED",
            },
        )

    async def task_reject():
        return await client.post(
            f"/api/v2/provider-trust/professional/{p_id}/reject",
            headers={**rev_headers, "Idempotency-Key": f"race-r-{suffix}"},
            json={
                "expected_version": 2,
                "decision_reason_code": "CONCURRENT_REJECT",
            },
        )

    res_verify, res_reject = await asyncio.gather(task_verify(), task_reject())
    statuses = [res_verify.status_code, res_reject.status_code]
    assert 200 in statuses
    assert 409 in statuses

    async with db_factory() as db:
        prof = await db.scalar(
            select(ProfessionalVerification).where(
                ProfessionalVerification.provider_id == p_id
            )
        )
        assert prof.version == 3


async def test_09_csrf_and_session_adversarial(qualification_client, db_factory):
    """Qualify CSRF cookie requirements, Bearer exemptions, and session security."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    fac_a_id = fixture["facility_a_id"]
    suffix = uuid.uuid4().hex[:8]

    reviewer_id = fixture["fac_reviewer_id"]
    session_token = await issue_provider_session_token(
        provider_id=reviewer_id,
        user_agent=_USER_AGENT,
        client_ip="127.0.0.1",
        mfa_verified_at=datetime.now(timezone.utc),
    )

    csrf_token = secrets.token_hex(16)
    facility_path = f"/api/v2/provider-trust/facilities/{fac_a_id}/submit"
    payload = {"expected_version": 1}

    # 1. Cookie session + missing CSRF -> 403
    resp_missing_csrf = await client.post(
        facility_path,
        cookies={"nexa_provider_session": session_token},
        headers={
            "Origin": "http://testserver",
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": f"csrf-1-{suffix}",
        },
        json=payload,
    )
    assert resp_missing_csrf.status_code == 403
    assert resp_missing_csrf.json() == {"error_code": "CSRF_TOKEN_REJECTED"}

    # 2. Cookie session + invalid CSRF -> 403
    resp_bad_csrf = await client.post(
        facility_path,
        cookies={
            "nexa_provider_session": session_token,
            "nexa_csrf": csrf_token,
        },
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "wrong-csrf-token",
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": f"csrf-2-{suffix}",
        },
        json=payload,
    )
    assert resp_bad_csrf.status_code == 403
    assert resp_bad_csrf.json() == {"error_code": "CSRF_TOKEN_REJECTED"}

    # 3. Cookie session + valid CSRF + valid origin -> reaches route
    resp_good_csrf = await client.post(
        facility_path,
        cookies={
            "nexa_provider_session": session_token,
            "nexa_csrf": csrf_token,
        },
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf_token,
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": f"csrf-3-{suffix}",
        },
        json=payload,
    )
    assert resp_good_csrf.status_code in {200, 409}

    # 4. Bearer session -> exempt from cookie CSRF
    resp_bearer = await client.post(
        facility_path,
        headers={
            "Authorization": f"Bearer {session_token}",
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": f"csrf-4-{suffix}",
        },
        json=payload,
    )
    assert resp_bearer.status_code in {200, 409}

    # 5. Basic auth -> 401
    resp_basic = await client.post(
        facility_path,
        headers={
            "Authorization": "Basic dXNlcjpwYXNz",
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": f"csrf-5-{suffix}",
        },
        json=payload,
    )
    assert resp_basic.status_code == 401


async def test_10_legacy_role_confusion_matrix(qualification_client, db_factory):
    """Explicitly qualify EACH legacy role with NO trust grant against organizational routes."""
    client = qualification_client
    fixture = await _seed_synthetic_fixture(db_factory)
    fac_a_id = fixture["facility_a_id"]
    suffix = uuid.uuid4().hex[:8]

    legacy_roles = [
        "admin",
        "privacy_officer",
        "auditor",
        "clinical_reviewer",
        "clinician",
        "receptionist",
    ]

    for role_name in legacy_roles:
        actor_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        email = f"legacy-{role_name}-{suffix}@example.test"

        async with db_factory() as db:
            db.add_all(
                [
                    ProviderIdentity(
                        id=actor_id,
                        provider_uid=f"legacy-{role_name}-{suffix}",
                        hospital_id=fac_a_id,
                        contact_email=email,
                        contact_phone="+919876543217",
                        email_verified_at=now,
                        phone_verified_at=now,
                        status="active",
                        is_active=True,
                    ),
                    ProviderCredential(
                        provider_id=actor_id,
                        login_identifier=email,
                        password_hash=hash_provider_password("Password123!"),
                        mfa_enabled=True,
                        is_active=True,
                    ),
                    ProviderHospitalAffiliation(
                        id=uuid.uuid4(),
                        provider_id=actor_id,
                        hospital_id=fac_a_id,
                        affiliation_type="PERMANENT",
                        roles=[role_name],
                        is_primary=True,
                        is_active=True,
                        trust_status=AffiliationTrustStatus.ACTIVE.value,
                        version=1,
                    ),
                ]
            )
            await db.commit()

        token = await issue_provider_session_token(
            provider_id=actor_id,
            user_agent=_USER_AGENT,
            client_ip="127.0.0.1",
            mfa_verified_at=now,
        )

        resp = await client.post(
            f"/api/v2/provider-trust/facilities/{fac_a_id}/submit",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": _USER_AGENT,
                "Idempotency-Key": f"legacy-{role_name}-{suffix}",
            },
            json={"expected_version": 1},
        )
        assert resp.status_code == 403, f"Expected 403 for role '{role_name}'"
        assert resp.json() == {"error_code": "AUTHORIZATION_DENIED"}


async def test_11_audit_outbox_secret_scan(db_factory):
    """Scan all records in audit_outbox across the journey and prove zero secrets or PII."""
    async with db_factory() as db:
        rows = (
            (await db.execute(text("SELECT payload FROM public.audit_outbox")))
            .scalars()
            .all()
        )
        assert len(rows) > 0

        prohibited_fragments = [
            "CandidateStrongPass123!",
            "SyntheticPass123!",
            "Password123!Safe",
            "nexa_provider_session",
            "Bearer ",
            "totp_secret",
        ]

        for payload in rows:
            dumped = json.dumps(payload)
            for secret in prohibited_fragments:
                assert (
                    secret not in dumped
                ), f"Prohibited secret fragment '{secret}' discovered in audit outbox row: {dumped}"


async def test_12_route_surface_audit():
    """Inspect FastAPI route table to confirm approved command routes exist and no unauthorized endpoints exist.

    Validates exact route surface contract:
    - Exactly 26 command-specific POST endpoints:
      - 24 Slice 3F lifecycle routes (1 professional self-submit + 9 professional reviewer + 8 facility + 6 affiliation)
      - 2 Slice 4E permission administration routes (grant + revoke)
    - Zero generic status PATCH, zero generic transition routes.
    - Zero begin_nested() calls in provider_contact_assurance_service.py (architectural invariant).
    """
    routes = [route for route in app.routes if hasattr(route, "path")]
    trust_routes = [r for r in routes if "/provider-trust" in r.path]
    assert (
        len(trust_routes) == 26
    ), f"Expected exactly 26 provider-trust routes, found {len(trust_routes)}"

    lifecycle_routes = [r for r in trust_routes if "/permissions" not in r.path]
    permission_routes = [r for r in trust_routes if "/permissions" in r.path]
    assert len(lifecycle_routes) == 24
    assert len(permission_routes) == 2

    prof_me_routes = [r for r in lifecycle_routes if "/professional/me" in r.path]
    assert len(prof_me_routes) == 1
    assert prof_me_routes[0].path == "/api/v2/provider-trust/professional/me/submit"

    prof_reviewer_routes = [
        r for r in lifecycle_routes if "/professional/{provider_id}" in r.path
    ]
    assert len(prof_reviewer_routes) == 9
    expected_prof_actions = {
        "verify",
        "reject",
        "mark-recheck-due",
        "suspend",
        "restore",
        "revoke",
        "mark-stale",
        "complete-recheck",
        "expire",
    }
    actual_prof_actions = {r.path.split("/")[-1] for r in prof_reviewer_routes}
    assert actual_prof_actions == expected_prof_actions

    fac_routes = [r for r in lifecycle_routes if "/facilities/{facility_id}" in r.path]
    assert len(fac_routes) == 8
    expected_fac_actions = {
        "submit",
        "verify",
        "reject",
        "mark-recheck-required",
        "suspend",
        "restore",
        "close",
        "complete-recheck",
    }
    actual_fac_actions = {r.path.split("/")[-1] for r in fac_routes}
    assert actual_fac_actions == expected_fac_actions

    affil_routes = [
        r for r in lifecycle_routes if "/affiliations/{affiliation_id}" in r.path
    ]
    assert len(affil_routes) == 6
    expected_affil_actions = {
        "activate",
        "suspend",
        "restore",
        "revoke",
        "leave",
        "expire",
    }
    actual_affil_actions = {r.path.split("/")[-1] for r in affil_routes}
    assert actual_affil_actions == expected_affil_actions

    for r in trust_routes:
        assert r.methods == {"POST"}
        assert "PATCH" not in r.methods
        assert "DELETE" not in r.methods
        assert "GET" not in r.methods

    for r in lifecycle_routes:
        assert "grant" not in r.path
        assert "admin" not in r.path
        assert "bootstrap" not in r.path
        assert "status" not in r.path
        assert "transition" not in r.path

    # Architectural invariant: provider_contact_assurance_service must NOT use begin_nested()
    service_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "app",
        "services",
        "provider_contact_assurance_service.py",
    )
    with open(service_path, "r", encoding="utf-8") as f:
        service_code = f.read()
    assert (
        "begin_nested" not in service_code
    ), "Forbidden begin_nested() discovered in provider_contact_assurance_service.py"
    assert (
        "async with db.begin():" in service_code
    ), "Expected top-level transaction ownership block 'async with db.begin():' missing from service"
