"""Disposable-PostgreSQL qualification for Provider Trust Slice 3A."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.provider import (
    FacilityVerification,
    HospitalRegistry,
    ProfessionalVerification,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.services.provider_registration_service import (
    ProviderBootstrapRequest,
    ProviderRegistrationError,
    bootstrap_provider_account,
)
from app.services.provider_auth_service import authenticate_provider_password

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

_HMAC_SECRET = "provider-registration-postgres-test-hmac-secret-000000000"


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if "127.0.0.1" not in value and "localhost" not in value:
        pytest.fail("TEST_DATABASE_URL must be loopback-only")
    return value


def _request(suffix: str, hospital_id: uuid.UUID) -> ProviderBootstrapRequest:
    return ProviderBootstrapRequest(
        display_name="Synthetic Bootstrap Provider",
        login_identifier=f"login-{suffix}@example.test",
        contact_email=f"contact-{suffix}@example.test",
        contact_phone="9876543210",
        password="synthetic-provider-bootstrap-password",
        hospital_id=hospital_id,
        registration_authority_code="TEST-COUNCIL",
        registration_number=f"REG-{suffix}",
    )


async def _counts(db, *, login_identifier: str) -> tuple[int, int, int]:
    providers = (
        await db.execute(
            select(func.count(ProviderIdentity.id)).where(
                ProviderIdentity.contact_email == login_identifier.replace("login-", "contact-")
            )
        )
    ).scalar_one()
    credentials = (
        await db.execute(
            select(func.count(ProviderCredential.id)).where(
                ProviderCredential.login_identifier == login_identifier
            )
        )
    ).scalar_one()
    professional = (
        await db.execute(
            select(func.count(ProfessionalVerification.id)).where(
                ProfessionalVerification.registration_number_normalized
                == login_identifier.split("@", 1)[0].replace("login-", "REG").upper()
            )
        )
    ).scalar_one()
    return providers, credentials, professional


async def _seed_facility(factory, suffix: str) -> uuid.UUID:
    async with factory() as db:
        hospital = HospitalRegistry(
            facility_code=f"BOOTSTRAP-{suffix[:16]}",
            legal_name="Synthetic Qualification Facility",
            display_name="Synthetic Qualification Facility",
            is_active=True,
        )
        db.add(hospital)
        await db.commit()
        return hospital.id


async def test_provider_bootstrap_is_atomic_untrusted_and_clinically_denied():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)
    request = _request(suffix, hospital_id)
    try:
        async with factory() as db:
            result = await bootstrap_provider_account(
                db,
                request=request,
                idempotency_key=f"provider-bootstrap-{suffix}",
                idempotency_hmac_secret=_HMAC_SECRET,
            )
            assert result.idempotent_replay is False

        async with factory() as db:
            provider = await db.scalar(
                select(ProviderIdentity).where(ProviderIdentity.id == uuid.UUID(result.provider_id))
            )
            credential = await db.scalar(
                select(ProviderCredential).where(ProviderCredential.provider_id == provider.id)
            )
            professional = await db.scalar(
                select(ProfessionalVerification).where(
                    ProfessionalVerification.provider_id == provider.id
                )
            )
            assert provider is not None
            assert provider.email_verified_at is None
            assert provider.phone_verified_at is None
            assert provider.role == "provider"
            assert credential is not None and credential.mfa_enabled is False
            assert professional is not None and professional.status == "NOT_SUBMITTED"
            assert (
                await db.scalar(
                    select(func.count(ProviderHospitalAffiliation.id)).where(
                        ProviderHospitalAffiliation.provider_id == provider.id
                    )
                )
                == 1
            )
            affiliation = await db.scalar(
                select(ProviderHospitalAffiliation).where(
                    ProviderHospitalAffiliation.provider_id == provider.id
                )
            )
            assert affiliation is not None
            assert affiliation.hospital_id == hospital_id
            assert affiliation.roles == []
            assert affiliation.trust_status == "PENDING_ACTIVATION"
            assert (
                await db.scalar(
                    select(func.count(FacilityVerification.id)).where(
                        FacilityVerification.facility_id == hospital_id
                    )
                )
                == 0
            )
            login = await authenticate_provider_password(
                db,
                request.login_identifier,
                request.password,
                hospital_id,
            )
            assert login.context is not None
            assert login.context.affiliation.roles == []
            result = await ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            ).evaluate_interactive(
                db,
                provider,
                InteractiveClinicalAuthentication(
                    provider_id=provider.id,
                    hospital_id=hospital_id,
                    method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                    session_authenticated=True,
                    mfa_verified_at=datetime.now(timezone.utc),
                ),
                ClinicalCapability.RECORD_READ,
            )
            assert result.allowed is False
            assert result.denial_code is ClinicalEligibilityDenialCode.CONTACT_VERIFICATION_REQUIRED
    finally:
        await engine.dispose()


async def test_provider_bootstrap_same_key_replays_and_mismatch_is_rejected():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)
    try:
        async with factory() as db:
            first = await bootstrap_provider_account(
                db,
                request=_request(suffix, hospital_id),
                idempotency_key=f"provider-replay-{suffix}",
                idempotency_hmac_secret=_HMAC_SECRET,
            )
        async with factory() as db:
            replay = await bootstrap_provider_account(
                db,
                request=_request(suffix, hospital_id),
                idempotency_key=f"provider-replay-{suffix}",
                idempotency_hmac_secret=_HMAC_SECRET,
            )
            assert replay.provider_id == first.provider_id
            assert replay.idempotent_replay is True
        async with factory() as db:
            with pytest.raises(ProviderRegistrationError) as exc_info:
                await bootstrap_provider_account(
                    db,
                    request=_request(suffix + "x", hospital_id),
                    idempotency_key=f"provider-replay-{suffix}",
                    idempotency_hmac_secret=_HMAC_SECRET,
                )
            assert exc_info.value.code == "IDEMPOTENCY_KEY_REUSED"
    finally:
        await engine.dispose()


async def test_provider_bootstrap_concurrent_duplicate_login_has_one_winner():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)

    async def create(key_suffix: str):
        async with factory() as db:
            return await bootstrap_provider_account(
                db,
                request=_request(suffix, hospital_id),
                idempotency_key=f"provider-race-{key_suffix}-{suffix}",
                idempotency_hmac_secret=_HMAC_SECRET,
            )

    try:
        outcomes = await asyncio.gather(create("one"), create("two"), return_exceptions=True)
        assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
        errors = [item for item in outcomes if isinstance(item, ProviderRegistrationError)]
        assert len(errors) == 1
        assert errors[0].code == "PROVIDER_REGISTRATION_CONFLICT"
    finally:
        await engine.dispose()


async def test_provider_bootstrap_same_idempotency_key_race_has_one_graph_and_one_replay():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)

    async def create():
        async with factory() as db:
            return await bootstrap_provider_account(
                db,
                request=_request(suffix, hospital_id),
                idempotency_key=f"provider-idempotency-race-{suffix}",
                idempotency_hmac_secret=_HMAC_SECRET,
            )

    try:
        outcomes = await asyncio.gather(create(), create())
        assert {item.idempotent_replay for item in outcomes} == {False, True}
        assert len({item.provider_id for item in outcomes}) == 1
    finally:
        await engine.dispose()


async def test_provider_bootstrap_duplicate_professional_registration_has_one_winner():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)
    first_request = _request(suffix, hospital_id)
    second_request = replace(
        _request(suffix + "second", hospital_id),
        registration_authority_code=first_request.registration_authority_code,
        registration_number=first_request.registration_number,
    )
    try:
        async with factory() as db:
            await bootstrap_provider_account(
                db,
                request=first_request,
                idempotency_key=f"provider-professional-first-{suffix}",
                idempotency_hmac_secret=_HMAC_SECRET,
            )
        async with factory() as db:
            with pytest.raises(ProviderRegistrationError) as exc_info:
                await bootstrap_provider_account(
                    db,
                    request=second_request,
                    idempotency_key=f"provider-professional-second-{suffix}",
                    idempotency_hmac_secret=_HMAC_SECRET,
                )
            assert exc_info.value.code == "PROVIDER_REGISTRATION_CONFLICT"
    finally:
        await engine.dispose()


async def test_provider_bootstrap_credential_creation_failure_rolls_back_identity():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)
    request = _request(suffix, hospital_id)
    try:
        async with factory() as db:
            with patch(
                "app.services.provider_registration_service.hash_provider_password",
                side_effect=RuntimeError("synthetic credential fault"),
            ):
                with pytest.raises(RuntimeError):
                    await bootstrap_provider_account(
                        db,
                        request=request,
                        idempotency_key=f"provider-credential-fault-{suffix}",
                        idempotency_hmac_secret=_HMAC_SECRET,
                    )
        async with factory() as db:
            assert await _counts(db, login_identifier=request.login_identifier) == (0, 0, 0)
    finally:
        await engine.dispose()


async def test_provider_bootstrap_identity_creation_failure_leaves_no_reservation():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)
    request = _request(suffix, hospital_id)
    try:
        async with factory() as db:
            with patch.object(
                db,
                "flush",
                AsyncMock(side_effect=SQLAlchemyError("synthetic identity fault")),
            ):
                with pytest.raises(SQLAlchemyError):
                    await bootstrap_provider_account(
                        db,
                        request=request,
                        idempotency_key=f"provider-identity-fault-{suffix}",
                        idempotency_hmac_secret=_HMAC_SECRET,
                    )
        async with factory() as db:
            assert await _counts(db, login_identifier=request.login_identifier) == (0, 0, 0)
            reservation = await db.scalar(
                text(
                    """SELECT count(*) FROM public.mutation_idempotency
                       WHERE tenant_id = 'platform-provider-registration'
                         AND operation = 'provider.bootstrap.v1'
                         AND idempotency_key = :idempotency_key"""
                ),
                {"idempotency_key": f"provider-identity-fault-{suffix}"},
            )
            assert reservation == 0
    finally:
        await engine.dispose()


async def test_provider_bootstrap_audit_failure_rolls_back_graph():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    hospital_id = await _seed_facility(factory, suffix)
    request = _request(suffix, hospital_id)
    try:
        async with factory() as db:
            with patch(
                "app.services.provider_registration_service.enqueue_audit_event",
                AsyncMock(side_effect=SQLAlchemyError("synthetic audit fault")),
            ):
                with pytest.raises(SQLAlchemyError):
                    await bootstrap_provider_account(
                        db,
                        request=request,
                        idempotency_key=f"provider-audit-fault-{suffix}",
                        idempotency_hmac_secret=_HMAC_SECRET,
                    )
        async with factory() as db:
            assert await _counts(db, login_identifier=request.login_identifier) == (0, 0, 0)
    finally:
        await engine.dispose()
