"""Disposable-PostgreSQL qualification for Provider Trust Slice 3B."""

from __future__ import annotations

import os
import uuid
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v2.auth_routes import ProviderMfaSetupVerifyRequest, provider_mfa_setup_verify
from app.models.provider import (
    ProviderContactVerificationChallenge,
    ProviderCredential,
    ProviderIdentity,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.services.provider_contact_assurance_service import (
    ProviderContactAssuranceError,
    issue_provider_contact_challenge,
    update_provider_contact,
    verify_provider_contact_challenge,
)
from app.services.provider_registration_service import (
    ProviderBootstrapRequest,
    bootstrap_provider_account,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

_SECRET = "provider-contact-assurance-postgres-test-secret-000000000000"
_REGISTRATION_SECRET = "provider-registration-postgres-test-hmac-secret-000000000"


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if "127.0.0.1" not in value and "localhost" not in value:
        pytest.fail("TEST_DATABASE_URL must be loopback-only")
    return value


class _CaptureTransport:
    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str, str]] = []

    def assert_ready(self) -> None:
        return None

    async def deliver(self, *, channel: str, destination: str, verifier: str) -> None:
        self.deliveries.append((channel, destination, verifier))


async def _provider(factory):
    from app.models.provider import HospitalRegistry

    suffix = uuid.uuid4().hex
    async with factory() as db:
        hospital = HospitalRegistry(
            facility_code=f"CONTACT-{suffix[:16]}",
            legal_name="Synthetic Contact Assurance Facility",
            display_name="Synthetic Contact Assurance Facility",
            is_active=True,
        )
        db.add(hospital)
        await db.commit()
        result = await bootstrap_provider_account(
            db,
            request=ProviderBootstrapRequest(
                display_name="Synthetic Contact Provider",
                login_identifier=f"login-{suffix}@example.test",
                contact_email=f"contact-{suffix}@example.test",
                contact_phone="9876543210",
                password="synthetic-provider-bootstrap-password",
                hospital_id=hospital.id,
            ),
            idempotency_key=f"contact-bootstrap-{suffix}",
            idempotency_hmac_secret=_REGISTRATION_SECRET,
        )
        return uuid.UUID(result.provider_id), hospital.id


def _audit(hospital_id: uuid.UUID) -> AuditContext:
    return AuditContext.for_tenant(tenant_id=str(hospital_id), domain=AuditDomain.AUTH)


async def _issue(factory, provider_id, hospital_id, channel="EMAIL"):
    transport = _CaptureTransport()
    async with factory() as db:
        issued = await issue_provider_contact_challenge(
            db,
            provider_id=provider_id,
            channel=channel,
            hmac_secret=_SECRET,
            audit_context=_audit(hospital_id),
            transport=transport,
        )
    return issued, transport.deliveries[-1][2]


async def test_email_phone_verification_preserves_clinical_denial():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, hospital_id = await _provider(factory)
        email, email_verifier = await _issue(factory, provider_id, hospital_id)
        phone, phone_verifier = await _issue(factory, provider_id, hospital_id, "PHONE")
        async with factory() as db:
            first = await verify_provider_contact_challenge(
                db,
                provider_id=provider_id,
                channel="EMAIL",
                challenge_id=email.challenge_id,
                verifier=email_verifier,
                idempotency_key=f"contact-email-verify-{uuid.uuid4().hex}",
                hmac_secret=_SECRET,
                audit_context=_audit(hospital_id),
            )
            provider = await db.scalar(
                select(ProviderIdentity).where(ProviderIdentity.id == provider_id)
            )
            intermediate = await ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            ).evaluate_interactive(
                db,
                provider,
                InteractiveClinicalAuthentication(
                    provider_id=provider_id,
                    hospital_id=hospital_id,
                    method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                    session_authenticated=True,
                    mfa_verified_at=datetime.now(timezone.utc),
                ),
                ClinicalCapability.RECORD_READ,
            )
            assert intermediate.allowed is False
            assert (
                intermediate.denial_code
                is ClinicalEligibilityDenialCode.CONTACT_VERIFICATION_REQUIRED
            )
            # The eligibility read starts SQLAlchemy's implicit read-only
            # transaction; close it before the next independently atomic
            # contact-assurance service transaction.
            await db.rollback()
            second = await verify_provider_contact_challenge(
                db,
                provider_id=provider_id,
                channel="PHONE",
                challenge_id=phone.challenge_id,
                verifier=phone_verifier,
                idempotency_key=f"contact-phone-verify-{uuid.uuid4().hex}",
                hmac_secret=_SECRET,
                audit_context=_audit(hospital_id),
            )
            assert not first.idempotent_replay and not second.idempotent_replay
        async with factory() as db:
            provider = await db.scalar(select(ProviderIdentity).where(ProviderIdentity.id == provider_id))
            assert provider.email_verified_at is not None
            assert provider.phone_verified_at is not None
            result = await ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            ).evaluate_interactive(
                db,
                provider,
                InteractiveClinicalAuthentication(
                    provider_id=provider_id,
                    hospital_id=hospital_id,
                    method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                    session_authenticated=True,
                    mfa_verified_at=datetime.now(timezone.utc),
                ),
                ClinicalCapability.RECORD_READ,
            )
            assert result.allowed is False
            assert (
                result.denial_code
                is ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_REQUIRED
            )
            credential = await db.scalar(
                select(ProviderCredential).where(ProviderCredential.provider_id == provider_id)
            )
            credential.mfa_enabled = True
            await db.commit()
        async with factory() as db:
            provider = await db.scalar(select(ProviderIdentity).where(ProviderIdentity.id == provider_id))
            result = await ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            ).evaluate_interactive(
                db,
                provider,
                InteractiveClinicalAuthentication(
                    provider_id=provider_id,
                    hospital_id=hospital_id,
                    method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                    session_authenticated=True,
                    mfa_verified_at=datetime.now(timezone.utc),
                ),
                ClinicalCapability.RECORD_READ,
            )
            assert result.allowed is False
            assert result.denial_code is ClinicalEligibilityDenialCode.PROFESSIONAL_VERIFICATION_REQUIRED
    finally:
        await engine.dispose()


async def test_replacement_binding_idempotency_and_contact_change_reset():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, hospital_id = await _provider(factory)
        old, old_verifier = await _issue(factory, provider_id, hospital_id)
        current, verifier = await _issue(factory, provider_id, hospital_id)
        async with factory() as db:
            with pytest.raises(ProviderContactAssuranceError) as invalidated:
                await verify_provider_contact_challenge(
                    db,
                    provider_id=provider_id,
                    channel="EMAIL",
                    challenge_id=old.challenge_id,
                    verifier=old_verifier,
                    idempotency_key=f"contact-old-{uuid.uuid4().hex}",
                    hmac_secret=_SECRET,
                    audit_context=_audit(hospital_id),
                )
            assert invalidated.value.code == "CONTACT_CHALLENGE_VERIFICATION_FAILED"
        key = f"contact-current-{uuid.uuid4().hex}"
        async with factory() as db:
            result = await verify_provider_contact_challenge(
                db,
                provider_id=provider_id,
                channel="EMAIL",
                challenge_id=current.challenge_id,
                verifier=verifier,
                idempotency_key=key,
                hmac_secret=_SECRET,
                audit_context=_audit(hospital_id),
            )
            assert result.idempotent_replay is False
        async with factory() as db:
            replay = await verify_provider_contact_challenge(
                db,
                provider_id=provider_id,
                channel="EMAIL",
                challenge_id=current.challenge_id,
                verifier=verifier,
                idempotency_key=key,
                hmac_secret=_SECRET,
                audit_context=_audit(hospital_id),
            )
            assert replay.idempotent_replay is True
        async with factory() as db:
            with pytest.raises(ProviderContactAssuranceError) as reused:
                await verify_provider_contact_challenge(
                    db,
                    provider_id=provider_id,
                    channel="EMAIL",
                    challenge_id=current.challenge_id,
                    verifier="different-verifier-material-that-is-long-enough",
                    idempotency_key=key,
                    hmac_secret=_SECRET,
                    audit_context=_audit(hospital_id),
                )
            assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"
        async with factory() as db:
            changed = await update_provider_contact(
                db,
                provider_id=provider_id,
                channel="EMAIL",
                value=f"replacement-{uuid.uuid4().hex}@example.test",
                idempotency_key=f"contact-change-{uuid.uuid4().hex}",
                hmac_secret=_SECRET,
                audit_context=_audit(hospital_id),
            )
            assert changed.idempotent_replay is False
        async with factory() as db:
            provider = await db.scalar(select(ProviderIdentity).where(ProviderIdentity.id == provider_id))
            assert provider.email_verified_at is None
            assert (
                await db.scalar(
                    select(func.count(ProviderContactVerificationChallenge.id)).where(
                        ProviderContactVerificationChallenge.provider_id == provider_id,
                        ProviderContactVerificationChallenge.invalidated_at.is_not(None),
                    )
                )
                >= 1
            )
    finally:
        await engine.dispose()


async def test_verification_audit_failure_rolls_back_authority_transition():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, hospital_id = await _provider(factory)
        issued, verifier = await _issue(factory, provider_id, hospital_id)
        async with factory() as db:
            with patch(
                "app.services.provider_contact_assurance_service.enqueue_audit_event",
                AsyncMock(side_effect=RuntimeError("outbox unavailable")),
            ):
                with pytest.raises(RuntimeError):
                    await verify_provider_contact_challenge(
                        db,
                        provider_id=provider_id,
                        channel="EMAIL",
                        challenge_id=issued.challenge_id,
                        verifier=verifier,
                        idempotency_key=f"contact-audit-{uuid.uuid4().hex}",
                        hmac_secret=_SECRET,
                        audit_context=_audit(hospital_id),
                    )
        async with factory() as db:
            provider = await db.scalar(select(ProviderIdentity).where(ProviderIdentity.id == provider_id))
            challenge = await db.scalar(
                select(ProviderContactVerificationChallenge).where(
                    ProviderContactVerificationChallenge.id == issued.challenge_id
                )
            )
            assert provider.email_verified_at is None
            assert challenge.consumed_at is None
            assert challenge.succeeded_at is None
    finally:
        await engine.dispose()


async def test_postgres_completion_and_same_key_races_have_one_transition():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, hospital_id = await _provider(factory)
        issued, verifier = await _issue(factory, provider_id, hospital_id)

        async def complete(key: str):
            async with factory() as db:
                return await verify_provider_contact_challenge(
                    db,
                    provider_id=provider_id,
                    channel="EMAIL",
                    challenge_id=issued.challenge_id,
                    verifier=verifier,
                    idempotency_key=key,
                    hmac_secret=_SECRET,
                    audit_context=_audit(hospital_id),
                )

        outcomes = await asyncio.gather(
            complete(f"contact-race-one-{uuid.uuid4().hex}"),
            complete(f"contact-race-two-{uuid.uuid4().hex}"),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
        assert sum(isinstance(value, ProviderContactAssuranceError) for value in outcomes) == 1

        repeat, repeat_verifier = await _issue(factory, provider_id, hospital_id)
        key = f"contact-same-key-{uuid.uuid4().hex}"
        outcomes = await asyncio.gather(
            complete_same_key(factory, provider_id, hospital_id, repeat, repeat_verifier, key),
            complete_same_key(factory, provider_id, hospital_id, repeat, repeat_verifier, key),
        )
        assert sorted(result.idempotent_replay for result in outcomes) == [False, True]
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.audit_outbox "
                    "WHERE event_type = 'PROVIDER_CONTACT_EMAIL_VERIFIED' "
                    "AND actor_id = :actor"
                ),
                {"actor": str(provider_id)},
            )
            assert count == 2
    finally:
        await engine.dispose()


async def complete_same_key(factory, provider_id, hospital_id, issued, verifier, key):
    async with factory() as db:
        return await verify_provider_contact_challenge(
            db,
            provider_id=provider_id,
            channel="EMAIL",
            challenge_id=issued.challenge_id,
            verifier=verifier,
            idempotency_key=key,
            hmac_secret=_SECRET,
            audit_context=_audit(hospital_id),
        )


async def test_postgres_contact_change_race_cannot_verify_replacement_contact():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, hospital_id = await _provider(factory)
        issued, verifier = await _issue(factory, provider_id, hospital_id)
        replacement = f"replacement-{uuid.uuid4().hex}@example.test"

        async def verify():
            async with factory() as db:
                return await verify_provider_contact_challenge(
                    db,
                    provider_id=provider_id,
                    channel="EMAIL",
                    challenge_id=issued.challenge_id,
                    verifier=verifier,
                    idempotency_key=f"contact-race-verify-{uuid.uuid4().hex}",
                    hmac_secret=_SECRET,
                    audit_context=_audit(hospital_id),
                )

        async def change():
            async with factory() as db:
                return await update_provider_contact(
                    db,
                    provider_id=provider_id,
                    channel="EMAIL",
                    value=replacement,
                    idempotency_key=f"contact-race-change-{uuid.uuid4().hex}",
                    hmac_secret=_SECRET,
                    audit_context=_audit(hospital_id),
                )

        outcomes = await asyncio.gather(verify(), change(), return_exceptions=True)
        assert any(not isinstance(value, BaseException) for value in outcomes)
        async with factory() as db:
            provider = await db.scalar(select(ProviderIdentity).where(ProviderIdentity.id == provider_id))
            assert provider.contact_email == replacement
            assert provider.email_verified_at is None
    finally:
        await engine.dispose()


async def test_postgres_mfa_enable_race_stages_one_authority_audit():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, hospital_id = await _provider(factory)
        async with factory() as db:
            credential = await db.scalar(
                select(ProviderCredential).where(ProviderCredential.provider_id == provider_id)
            )
            credential.mfa_secret_encrypted = "synthetic-ciphertext"
            await db.commit()
        context = type(
            "ProviderContext",
            (),
            {"actor_uid": "synthetic-provider", "provider": type("P", (), {"provider_id": provider_id})()},
        )()

        async def enable():
            async with factory() as db:
                return await provider_mfa_setup_verify(
                    ProviderMfaSetupVerifyRequest(totp_code="123456"), db, context
                )

        with (
            patch("app.api.v2.auth_routes.decrypt_mfa_secret", return_value="secret"),
            patch(
                "app.services.provider_auth_service.verify_totp_code_once",
                AsyncMock(return_value=True),
            ),
            patch("app.api.v2.auth_routes.get_async_redis_client", return_value=object()),
            patch("app.api.v2.auth_routes.current_audit_context", return_value=_audit(hospital_id)),
        ):
            outcomes = await asyncio.gather(enable(), enable())
        assert sorted(item["message"] for item in outcomes) == [
            "MFA has been successfully enabled.",
            "MFA is already enabled.",
        ]
        async with factory() as db:
            credential = await db.scalar(
                select(ProviderCredential).where(ProviderCredential.provider_id == provider_id)
            )
            assert credential.mfa_enabled is True
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.audit_outbox "
                    "WHERE event_type = 'PROVIDER_MFA_SETUP_SUCCESS' "
                    "AND idempotency_key = :key"
                ),
                {"key": f"provider-mfa-setup-success:{provider_id}"},
            )
            assert count == 1
    finally:
        await engine.dispose()


async def test_postgres_mfa_audit_stage_failure_rolls_back_authority_enablement():
    """F-01: durable outbox failure cannot leave MFA enabled in PostgreSQL."""

    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, hospital_id = await _provider(factory)
        async with factory() as db:
            credential = await db.scalar(
                select(ProviderCredential).where(ProviderCredential.provider_id == provider_id)
            )
            credential.mfa_secret_encrypted = "synthetic-ciphertext"
            credential.mfa_enabled = False
            await db.commit()
        context = type(
            "ProviderContext",
            (),
            {
                "actor_uid": "synthetic-provider",
                "provider": type("P", (), {"provider_id": provider_id})(),
            },
        )()

        async with factory() as db:
            with (
                patch("app.api.v2.auth_routes.decrypt_mfa_secret", return_value="secret"),
                patch(
                    "app.services.provider_auth_service.verify_totp_code_once",
                    AsyncMock(return_value=True),
                ),
                patch("app.api.v2.auth_routes.get_async_redis_client", return_value=object()),
                patch(
                    "app.api.v2.auth_routes.current_audit_context",
                    return_value=_audit(hospital_id),
                ),
                patch(
                    "app.api.v2.auth_routes.enqueue_audit_event",
                    AsyncMock(side_effect=RuntimeError("outbox unavailable")),
                ) as audit_stage,
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await provider_mfa_setup_verify(
                        ProviderMfaSetupVerifyRequest(totp_code="123456"), db, context
                    )
                assert exc_info.value.status_code == 503
                audit_stage.assert_awaited_once()

        async with factory() as db:
            credential = await db.scalar(
                select(ProviderCredential).where(ProviderCredential.provider_id == provider_id)
            )
            assert credential.mfa_enabled is False
            success_events = await db.scalar(
                text(
                    "SELECT count(*) FROM public.audit_outbox "
                    "WHERE event_type = 'PROVIDER_MFA_SETUP_SUCCESS' "
                    "AND idempotency_key = :key"
                ),
                {"key": f"provider-mfa-setup-success:{provider_id}"},
            )
            assert success_events == 0
    finally:
        await engine.dispose()
