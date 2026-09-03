"""Disposable PostgreSQL proof for Phase-3E atomic professional submission."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.services.clinical_eligibility import (
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.provider_trust_authorization import TrustManagementAuthentication
from app.services.provider_trust_lifecycle import (
    AffiliationTransitionCommand,
    AffiliationTransitionFacts,
    FacilityTransitionCommand,
    FacilityTransitionFacts,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
)
from app.services.provider_trust_lifecycle_application import (
    ProviderTrustLifecycleApplicationError,
    ProviderTrustLifecycleApplicationService,
)
import app.services.provider_trust_lifecycle_application as lifecycle_application


pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
HEAD = "20260903_trust_authorization"


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


async def _resource(factory):
    now = datetime.now(timezone.utc)
    facility_id, provider_id, verification_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=facility_id,
                facility_code=f"E3-{facility_id.hex[:12]}",
                legal_name="Synthetic",
                display_name="Synthetic",
                country_code="IN",
                is_active=True,
            )
        )
        provider = ProviderIdentity(
            id=provider_id,
            provider_uid=f"e3-{provider_id.hex}",
            hospital_id=facility_id,
            contact_email=f"e3-{provider_id.hex}@example.test",
            contact_phone="+910000000000",
            email_verified_at=now,
            phone_verified_at=now,
            status="active",
            is_active=True,
        )
        db.add_all(
            (
                provider,
                ProviderCredential(
                    provider_id=provider_id,
                    login_identifier=f"e3-{provider_id.hex}@example.test",
                    password_hash="synthetic-not-a-secret",
                    mfa_enabled=True,
                    is_active=True,
                ),
                ProfessionalVerification(
                    id=verification_id,
                    provider_id=provider_id,
                    status="NOT_SUBMITTED",
                    version=1,
                    previous_verification_valid=False,
                ),
            )
        )
        await db.commit()
    return provider_id, verification_id, now


async def _trusted_reviewer(db, facility_id, now, permissions):
    provider_id = uuid.uuid4()
    provider = ProviderIdentity(
        id=provider_id,
        provider_uid=f"reviewer-{provider_id.hex}",
        hospital_id=facility_id,
        contact_email=f"reviewer-{provider_id.hex}@example.test",
        contact_phone="+910000000000",
        email_verified_at=now,
        phone_verified_at=now,
        status="active",
        is_active=True,
    )
    db.add_all(
        (
            provider,
            ProviderCredential(
                provider_id=provider_id,
                login_identifier=f"reviewer-{provider_id.hex}@example.test",
                password_hash="synthetic-not-a-secret",
                mfa_enabled=True,
                is_active=True,
            ),
            *(
                ProviderTrustPermissionGrant(
                    provider_id=provider_id,
                    permission=permission,
                    scope_type="GLOBAL" if permission == "PROFESSIONAL_REVIEW" else "FACILITY",
                    facility_id=(
                        None if permission == "PROFESSIONAL_REVIEW" else facility_id
                    ),
                    granted_by_actor_id="synthetic-governance",
                )
                for permission in permissions
            ),
        )
    )
    return provider_id


def _professional_verify_facts(registration_number="REG-VERIFIED"):
    return ProfessionalTransitionFacts(
        registration_authority_code="AUTH",
        registration_number_normalized=registration_number,
        verification_method="synthetic-review",
        verification_source="synthetic-source",
        verification_reference="synthetic-reference",
        identity_binding_method="synthetic-binding",
        identity_binding_status="MATCHED",
    )


async def _outbox_target_count(db, target_id):
    return await db.scalar(
        text(
            "SELECT count(*) FROM public.audit_outbox "
            "WHERE payload ->> 'target_id' = :target_id"
        ),
        {"target_id": str(target_id)},
    )


async def _outbox_payload(db, target_id):
    return await db.scalar(
        text(
            "SELECT payload FROM public.audit_outbox "
            "WHERE payload ->> 'target_id' = :target_id"
        ),
        {"target_id": str(target_id)},
    )


async def _reviewable_professional(factory, *, prefix: str):
    """Create one active reviewer grant and a distinct pending target."""
    now = datetime.now(timezone.utc)
    facility_id, subject_id, verification_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=facility_id,
                facility_code=f"{prefix}-{facility_id.hex[:12]}",
                legal_name="Synthetic",
                display_name="Synthetic",
                country_code="IN",
                is_active=True,
            )
        )
        db.add_all(
            (
                ProviderIdentity(
                    id=subject_id,
                    provider_uid=f"{prefix}-subject-{subject_id.hex}",
                    status="active",
                    is_active=True,
                ),
                ProfessionalVerification(
                    id=verification_id,
                    provider_id=subject_id,
                    registration_authority_code="AUTH",
                    registration_number_normalized=f"REG-{verification_id.hex}",
                    status="PENDING_REVIEW",
                    version=1,
                    previous_verification_valid=False,
                ),
            )
        )
        reviewer_id = await _trusted_reviewer(
            db, facility_id, now, ("PROFESSIONAL_REVIEW",)
        )
        grant_id = await db.scalar(
            select(ProviderTrustPermissionGrant.id).where(
                ProviderTrustPermissionGrant.provider_id == reviewer_id
            )
        )
        await db.commit()
    assert grant_id is not None
    return now, reviewer_id, verification_id, grant_id


def _auth(provider_id, now):
    return TrustManagementAuthentication(
        provider_id=provider_id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=now,
    )


def _facts():
    return ProfessionalTransitionFacts(
        registration_authority_code="AUTH",
        registration_number_normalized="REG-1",
    )


async def test_submission_replay_and_outbox_atomicity(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, verification_id, now = await _resource(factory)
        async with factory() as db:
            result = await ProviderTrustLifecycleApplicationService(
                db
            ).apply_professional(
                actor_id=provider_id,
                authentication=_auth(provider_id, now),
                resource_id=verification_id,
                command=ProfessionalTransitionCommand.SUBMIT,
                facts=_facts(),
                expected_version=1,
                idempotency_key="phase3e-submit-0001",
                now=now,
            )
            assert result.new_state == "PENDING_REVIEW" and result.version == 2
        async with factory() as db:
            replay = await ProviderTrustLifecycleApplicationService(
                db
            ).apply_professional(
                actor_id=provider_id,
                authentication=_auth(provider_id, now),
                resource_id=verification_id,
                command=ProfessionalTransitionCommand.SUBMIT,
                facts=_facts(),
                expected_version=1,
                idempotency_key="phase3e-submit-0001",
                now=now,
            )
            assert replay.idempotent_replay
            row = await db.get(ProfessionalVerification, verification_id)
            assert row.status == "PENDING_REVIEW" and row.version == 2
            assert (
                await db.scalar(text("SELECT count(*) FROM public.audit_outbox")) == 1
            )
        provider_id, verification_id, now = await _resource(factory)
        async with factory() as db:
            with patch(
                "app.services.provider_trust_lifecycle_application.enqueue_audit_event",
                AsyncMock(side_effect=RuntimeError("synthetic audit failure")),
            ):
                with pytest.raises(ProviderTrustLifecycleApplicationError) as failure:
                    await ProviderTrustLifecycleApplicationService(
                        db
                    ).apply_professional(
                        actor_id=provider_id,
                        authentication=_auth(provider_id, now),
                        resource_id=verification_id,
                        command=ProfessionalTransitionCommand.SUBMIT,
                        facts=_facts(),
                        expected_version=1,
                        idempotency_key="phase3e-audit-failure-0001",
                        now=now,
                    )
                assert failure.value.code == "TRANSACTION_INTEGRITY_FAILURE"
        async with factory() as db:
            row = await db.get(ProfessionalVerification, verification_id)
            assert row.status == "NOT_SUBMITTED" and row.version == 1
            assert (
                await db.scalar(text("SELECT count(*) FROM public.audit_outbox")) == 1
            )
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.mutation_idempotency WHERE idempotency_key = 'phase3e-audit-failure-0001'"
                    )
                )
                == 0
            )
        provider_id, verification_id, now = await _resource(factory)
        async with factory() as db:
            with patch(
                "app.services.provider_trust_lifecycle_application._IDEMPOTENCY_COMPLETE",
                text("SELECT 1 / 0"),
            ):
                with pytest.raises(ProviderTrustLifecycleApplicationError) as failure:
                    await ProviderTrustLifecycleApplicationService(
                        db
                    ).apply_professional(
                        actor_id=provider_id,
                        authentication=_auth(provider_id, now),
                        resource_id=verification_id,
                        command=ProfessionalTransitionCommand.SUBMIT,
                        facts=_facts(),
                        expected_version=1,
                        idempotency_key="phase3e-completion-failure-0001",
                        now=now,
                    )
                assert failure.value.code == "TRANSACTION_INTEGRITY_FAILURE"
        async with factory() as db:
            row = await db.get(ProfessionalVerification, verification_id)
            assert row.status == "NOT_SUBMITTED" and row.version == 1
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.audit_outbox "
                        "WHERE event_type = 'PROVIDER_PROFESSIONAL_VERIFICATION_SUBMITTED'"
                    )
                )
                == 1
            )
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.mutation_idempotency "
                        "WHERE idempotency_key = 'phase3e-completion-failure-0001'"
                    )
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_same_key_concurrent_replay_has_one_transition_and_one_audit(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider_id, verification_id, now = await _resource(factory)

        async def submit():
            async with factory() as db:
                return await ProviderTrustLifecycleApplicationService(
                    db
                ).apply_professional(
                    actor_id=provider_id,
                    authentication=_auth(provider_id, now),
                    resource_id=verification_id,
                    command=ProfessionalTransitionCommand.SUBMIT,
                    facts=ProfessionalTransitionFacts(
                        registration_authority_code="AUTH",
                        registration_number_normalized=f"REG-{verification_id.hex}",
                    ),
                    expected_version=1,
                    idempotency_key="phase3e-concurrent-replay-0001",
                    now=now,
                )

        first, second = await asyncio.gather(submit(), submit())
        assert {first.idempotent_replay, second.idempotent_replay} == {False, True}
        assert first.version == second.version == 2
        async with factory() as db:
            row = await db.get(ProfessionalVerification, verification_id)
            assert row.status == "PENDING_REVIEW" and row.version == 2
            assert await _outbox_target_count(db, verification_id) == 1
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.mutation_idempotency "
                        "WHERE idempotency_key = 'phase3e-concurrent-replay-0001' "
                        "AND response_status = 200"
                    )
                )
                == 1
            )
    finally:
        await engine.dispose()


async def test_professional_reviewers_at_one_version_linearize_to_one_decision(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        facility_id, subject_id, verification_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            db.add(
                HospitalRegistry(
                    id=facility_id,
                    facility_code=f"E3C-{facility_id.hex[:12]}",
                    legal_name="Synthetic",
                    display_name="Synthetic",
                    country_code="IN",
                    is_active=True,
                )
            )
            db.add_all(
                (
                    ProviderIdentity(
                        id=subject_id,
                        provider_uid=f"subject-{subject_id.hex}",
                        status="active",
                        is_active=True,
                    ),
                    ProfessionalVerification(
                        id=verification_id,
                        provider_id=subject_id,
                        registration_authority_code="AUTH",
                        registration_number_normalized=f"REG-{verification_id.hex}",
                        status="PENDING_REVIEW",
                        version=1,
                        previous_verification_valid=False,
                    ),
                )
            )
            reviewer_id = await _trusted_reviewer(
                db, facility_id, now, ("PROFESSIONAL_REVIEW",)
            )
            await db.commit()

        async def attempt(command_value, facts, key):
            async with factory() as db:
                return await ProviderTrustLifecycleApplicationService(
                    db
                ).apply_professional(
                    actor_id=reviewer_id,
                    authentication=_auth(reviewer_id, now),
                    resource_id=verification_id,
                    command=command_value,
                    facts=facts,
                    expected_version=1,
                    idempotency_key=key,
                    now=now,
                )

        outcomes = await asyncio.gather(
            attempt(
                ProfessionalTransitionCommand.VERIFY,
                _professional_verify_facts(),
                "phase3e-professional-race-verify-0001",
            ),
            attempt(
                ProfessionalTransitionCommand.REJECT,
                ProfessionalTransitionFacts(decision_reason_code="SYNTHETIC_REJECT"),
                "phase3e-professional-race-reject-0001",
            ),
            return_exceptions=True,
        )
        successes = [item for item in outcomes if not isinstance(item, Exception)]
        failures = [item for item in outcomes if isinstance(item, Exception)]
        assert len(successes) == len(failures) == 1
        assert isinstance(failures[0], ProviderTrustLifecycleApplicationError)
        assert failures[0].code == "LIFECYCLE_VERSION_CONFLICT"
        async with factory() as db:
            verification = await db.get(ProfessionalVerification, verification_id)
            assert verification.status in {"VERIFIED", "REJECTED"}
            assert verification.version == 2
            assert await _outbox_target_count(db, verification_id) == 1
            payload = await _outbox_payload(db, verification_id)
            assert payload["audit_domain"] == "platform"
            assert payload["hospital_id"] is None and payload["tenant_id"] is None
    finally:
        await engine.dispose()


async def test_revoked_grant_cannot_authorize_a_subsequent_locked_transition(
    monkeypatch,
):
    """A fresh transaction must not reuse a pre-revocation grant decision."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        facility_id, subject_id, verification_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            db.add(
                HospitalRegistry(
                    id=facility_id,
                    facility_code=f"E3R-{facility_id.hex[:12]}",
                    legal_name="Synthetic",
                    display_name="Synthetic",
                    country_code="IN",
                    is_active=True,
                )
            )
            db.add_all(
                (
                    ProviderIdentity(
                        id=subject_id,
                        provider_uid=f"revocation-subject-{subject_id.hex}",
                        status="active",
                        is_active=True,
                    ),
                    ProfessionalVerification(
                        id=verification_id,
                        provider_id=subject_id,
                        registration_authority_code="AUTH",
                        registration_number_normalized=f"REG-{verification_id.hex}",
                        status="PENDING_REVIEW",
                        version=1,
                        previous_verification_valid=False,
                    ),
                )
            )
            reviewer_id = await _trusted_reviewer(
                db, facility_id, now, ("PROFESSIONAL_REVIEW",)
            )
            grant_id = await db.scalar(
                select(ProviderTrustPermissionGrant.id).where(
                    ProviderTrustPermissionGrant.provider_id == reviewer_id
                )
            )
            await db.commit()
        async with factory() as db:
            await db.execute(
                update(ProviderTrustPermissionGrant)
                .where(ProviderTrustPermissionGrant.id == grant_id)
                .values(revoked_at=now)
            )
            await db.commit()
        async with factory() as db:
            with pytest.raises(ProviderTrustLifecycleApplicationError) as denial:
                await ProviderTrustLifecycleApplicationService(db).apply_professional(
                    actor_id=reviewer_id,
                    authentication=_auth(reviewer_id, now),
                    resource_id=verification_id,
                    command=ProfessionalTransitionCommand.REJECT,
                    facts=ProfessionalTransitionFacts(
                        decision_reason_code="SYNTHETIC_REJECT"
                    ),
                    expected_version=1,
                    idempotency_key="phase3e-revoked-grant-0001",
                    now=now,
                )
            assert denial.value.code == "AUTHORIZATION_DENIED"
        async with factory() as db:
            verification = await db.get(ProfessionalVerification, verification_id)
            assert verification.status == "PENDING_REVIEW" and verification.version == 1
            assert await _outbox_target_count(db, verification_id) == 0
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.mutation_idempotency "
                        "WHERE idempotency_key = 'phase3e-revoked-grant-0001'"
                    )
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_grant_revocation_race_linearizes_transition_before_revocation(
    monkeypatch,
):
    """A revoker waits behind the transition's live ``FOR UPDATE`` grant lock."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    transition_locked = asyncio.Event()
    release_transition = asyncio.Event()
    revocation_attempted = asyncio.Event()
    try:
        now, reviewer_id, verification_id, grant_id = await _reviewable_professional(
            factory, prefix="E3RA"
        )
        original_enqueue = lifecycle_application.enqueue_audit_event

        async def hold_after_locked_authorization(*args, **kwargs):
            await original_enqueue(*args, **kwargs)
            transition_locked.set()
            await release_transition.wait()

        async def transition():
            async with factory() as db:
                return await ProviderTrustLifecycleApplicationService(
                    db
                ).apply_professional(
                    actor_id=reviewer_id,
                    authentication=_auth(reviewer_id, now),
                    resource_id=verification_id,
                    command=ProfessionalTransitionCommand.REJECT,
                    facts=ProfessionalTransitionFacts(
                        decision_reason_code="SYNTHETIC_REJECT"
                    ),
                    expected_version=1,
                    idempotency_key="phase3e-revocation-order-a-0001",
                    now=now,
                )

        async def revoke():
            async with factory() as db:
                revocation_attempted.set()
                await db.execute(
                    update(ProviderTrustPermissionGrant)
                    .where(ProviderTrustPermissionGrant.id == grant_id)
                    .values(revoked_at=now)
                )
                await db.commit()

        with patch(
            "app.services.provider_trust_lifecycle_application.enqueue_audit_event",
            new=hold_after_locked_authorization,
        ):
            transition_task = asyncio.create_task(transition())
            await asyncio.wait_for(transition_locked.wait(), timeout=5)
            revocation_task = asyncio.create_task(revoke())
            await asyncio.wait_for(revocation_attempted.wait(), timeout=5)
            await asyncio.sleep(0.1)
            assert not revocation_task.done()
            release_transition.set()
            result = await asyncio.wait_for(transition_task, timeout=5)
            await asyncio.wait_for(revocation_task, timeout=5)
        assert result.version == 2 and not result.idempotent_replay
        async with factory() as db:
            verification = await db.get(ProfessionalVerification, verification_id)
            grant = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert verification.status == "REJECTED" and verification.version == 2
            assert grant.revoked_at == now
            assert await _outbox_target_count(db, verification_id) == 1
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.mutation_idempotency "
                        "WHERE idempotency_key = 'phase3e-revocation-order-a-0001' "
                        "AND response_status = 200"
                    )
                )
                == 1
            )
    finally:
        release_transition.set()
        await engine.dispose()


async def test_grant_revocation_race_linearizes_revocation_before_transition(
    monkeypatch,
):
    """A transition resumed after revocation must evaluate the revoked lock row."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    revocation_locked = asyncio.Event()
    release_revocation = asyncio.Event()
    transition_started = asyncio.Event()
    try:
        now, reviewer_id, verification_id, grant_id = await _reviewable_professional(
            factory, prefix="E3RB"
        )

        async def revoke_then_commit():
            async with factory() as db:
                async with db.begin():
                    await db.execute(
                        update(ProviderTrustPermissionGrant)
                        .where(ProviderTrustPermissionGrant.id == grant_id)
                        .values(revoked_at=now)
                    )
                    revocation_locked.set()
                    await release_revocation.wait()

        async def transition():
            transition_started.set()
            async with factory() as db:
                return await ProviderTrustLifecycleApplicationService(
                    db
                ).apply_professional(
                    actor_id=reviewer_id,
                    authentication=_auth(reviewer_id, now),
                    resource_id=verification_id,
                    command=ProfessionalTransitionCommand.REJECT,
                    facts=ProfessionalTransitionFacts(
                        decision_reason_code="SYNTHETIC_REJECT"
                    ),
                    expected_version=1,
                    idempotency_key="phase3e-revocation-order-b-0001",
                    now=now,
                )

        revocation_task = asyncio.create_task(revoke_then_commit())
        await asyncio.wait_for(revocation_locked.wait(), timeout=5)
        transition_task = asyncio.create_task(transition())
        await asyncio.wait_for(transition_started.wait(), timeout=5)
        await asyncio.sleep(0.1)
        assert not transition_task.done()
        release_revocation.set()
        await asyncio.wait_for(revocation_task, timeout=5)
        with pytest.raises(ProviderTrustLifecycleApplicationError) as denial:
            await asyncio.wait_for(transition_task, timeout=5)
        assert denial.value.code == "AUTHORIZATION_DENIED"
        async with factory() as db:
            verification = await db.get(ProfessionalVerification, verification_id)
            grant = await db.get(ProviderTrustPermissionGrant, grant_id)
            assert verification.status == "PENDING_REVIEW" and verification.version == 1
            assert grant.revoked_at == now
            assert await _outbox_target_count(db, verification_id) == 0
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.mutation_idempotency "
                        "WHERE idempotency_key = 'phase3e-revocation-order-b-0001'"
                    )
                )
                == 0
            )
    finally:
        release_revocation.set()
        await engine.dispose()


async def test_facility_and_affiliation_versions_linearize_without_role_mutation(monkeypatch):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc)
        facility_id, facility_verification_id = uuid.uuid4(), uuid.uuid4()
        subject_id, affiliation_id = uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            db.add(
                HospitalRegistry(
                    id=facility_id,
                    facility_code=f"E3F-{facility_id.hex[:12]}",
                    legal_name="Synthetic",
                    display_name="Synthetic",
                    country_code="IN",
                    is_active=True,
                )
            )
            db.add_all(
                (
                    FacilityVerification(
                        id=facility_verification_id,
                        facility_id=facility_id,
                        status="PENDING_VERIFICATION",
                        version=1,
                    ),
                    ProviderIdentity(
                        id=subject_id,
                        provider_uid=f"affiliation-subject-{subject_id.hex}",
                        status="active",
                        is_active=True,
                    ),
                    ProviderHospitalAffiliation(
                        id=affiliation_id,
                        provider_id=subject_id,
                        hospital_id=facility_id,
                        roles=["clinician"],
                        trust_status="PENDING_ACTIVATION",
                        version=1,
                    ),
                )
            )
            reviewer_id = await _trusted_reviewer(
                db,
                facility_id,
                now,
                ("FACILITY_REVIEW", "AFFILIATION_MANAGE"),
            )
            await db.commit()

        async def facility_attempt(command_value, facts, key):
            async with factory() as db:
                return await ProviderTrustLifecycleApplicationService(db).apply_facility(
                    actor_id=reviewer_id,
                    authentication=_auth(reviewer_id, now),
                    resource_id=facility_verification_id,
                    command=command_value,
                    facts=facts,
                    expected_version=1,
                    idempotency_key=key,
                    now=now,
                )

        outcomes = await asyncio.gather(
            facility_attempt(
                FacilityTransitionCommand.VERIFY,
                FacilityTransitionFacts(
                    verification_method="synthetic-review",
                    verification_source="synthetic-source",
                    verification_reference="synthetic-reference",
                ),
                "phase3e-facility-race-verify-0001",
            ),
            facility_attempt(
                FacilityTransitionCommand.REJECT,
                FacilityTransitionFacts(decision_reason_code="SYNTHETIC_REJECT"),
                "phase3e-facility-race-reject-0001",
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert any(
            isinstance(item, ProviderTrustLifecycleApplicationError)
            and item.code == "LIFECYCLE_VERSION_CONFLICT"
            for item in outcomes
        )

        async def affiliation_attempt(key):
            async with factory() as db:
                return await ProviderTrustLifecycleApplicationService(
                    db
                ).apply_affiliation(
                    actor_id=reviewer_id,
                    authentication=_auth(reviewer_id, now),
                    resource_id=affiliation_id,
                    command=AffiliationTransitionCommand.ACTIVATE,
                    facts=AffiliationTransitionFacts(valid_from=now),
                    expected_version=1,
                    idempotency_key=key,
                    now=now,
                )

        outcomes = await asyncio.gather(
            affiliation_attempt("phase3e-affiliation-race-0001"),
            affiliation_attempt("phase3e-affiliation-race-0002"),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert any(
            isinstance(item, ProviderTrustLifecycleApplicationError)
            and item.code == "LIFECYCLE_VERSION_CONFLICT"
            for item in outcomes
        )
        async with factory() as db:
            facility = await db.get(FacilityVerification, facility_verification_id)
            affiliation = await db.get(ProviderHospitalAffiliation, affiliation_id)
            assert facility.status in {"VERIFIED", "REJECTED"} and facility.version == 2
            assert affiliation.trust_status == "ACTIVE" and affiliation.version == 2
            assert affiliation.roles == ["clinician"]
            assert await _outbox_target_count(db, facility_verification_id) == 1
            assert await _outbox_target_count(db, affiliation_id) == 1
            facility_payload = await _outbox_payload(db, facility_verification_id)
            affiliation_payload = await _outbox_payload(db, affiliation_id)
            assert facility_payload["audit_domain"] == "platform"
            assert affiliation_payload["audit_domain"] == "platform"
            assert facility_payload["hospital_id"] == str(facility_id)
            assert affiliation_payload["hospital_id"] == str(facility_id)
    finally:
        await engine.dispose()


async def test_each_lifecycle_suspension_immediately_denies_clinical_eligibility(
    monkeypatch,
):
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    eligibility = ClinicalEligibilityService(
        contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
    )
    try:
        now = datetime.now(timezone.utc)
        facility_id = uuid.uuid4()
        subject_id = uuid.uuid4()
        professional_id = uuid.uuid4()
        facility_verification_id = uuid.uuid4()
        affiliation_id = uuid.uuid4()
        registration_number = f"REG-{professional_id.hex}"
        async with factory() as db:
            db.add(
                HospitalRegistry(
                    id=facility_id,
                    facility_code=f"E3E-{facility_id.hex[:12]}",
                    legal_name="Synthetic",
                    display_name="Synthetic",
                    country_code="IN",
                    is_active=True,
                )
            )
            db.add_all(
                (
                    ProviderIdentity(
                        id=subject_id,
                        provider_uid=f"clinical-subject-{subject_id.hex}",
                        hospital_id=facility_id,
                        contact_email=f"clinical-subject-{subject_id.hex}@example.test",
                        contact_phone="+910000000000",
                        email_verified_at=now,
                        phone_verified_at=now,
                        status="active",
                        is_active=True,
                    ),
                    ProviderCredential(
                        provider_id=subject_id,
                        login_identifier=f"clinical-subject-{subject_id.hex}@example.test",
                        password_hash="synthetic-not-a-secret",
                        mfa_enabled=True,
                        is_active=True,
                    ),
                    ProfessionalVerification(
                        id=professional_id,
                        provider_id=subject_id,
                        registration_authority_code="AUTH",
                        registration_number_normalized=registration_number,
                        verification_method="synthetic-review",
                        verification_source="synthetic-source",
                        verification_reference="synthetic-reference",
                        identity_binding_method="synthetic-binding",
                        identity_binding_status="MATCHED",
                        verified_at=now,
                        last_checked_at=now,
                        previous_verification_valid=True,
                        status="VERIFIED",
                        version=1,
                    ),
                    FacilityVerification(
                        id=facility_verification_id,
                        facility_id=facility_id,
                        verification_method="synthetic-review",
                        verification_source="synthetic-source",
                        verification_reference="synthetic-reference",
                        verified_at=now,
                        last_checked_at=now,
                        status="VERIFIED",
                        version=1,
                    ),
                    ProviderHospitalAffiliation(
                        id=affiliation_id,
                        provider_id=subject_id,
                        hospital_id=facility_id,
                        roles=["clinician"],
                        trust_status="ACTIVE",
                        valid_from=now,
                        version=1,
                    ),
                )
            )
            reviewer_id = await _trusted_reviewer(
                db,
                facility_id,
                now,
                (
                    "PROFESSIONAL_REVIEW",
                    "FACILITY_REVIEW",
                    "AFFILIATION_MANAGE",
                ),
            )
            await db.commit()

        async def evaluate():
            async with factory() as db:
                subject = await db.get(ProviderIdentity, subject_id)
                return await eligibility.evaluate_interactive(
                    db,
                    subject,
                    InteractiveClinicalAuthentication(
                        provider_id=subject_id,
                        hospital_id=facility_id,
                        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                        session_authenticated=True,
                        mfa_verified_at=now,
                    ),
                    ClinicalCapability.DOCUMENTS_REVIEW,
                    now=now,
                )

        assert (await evaluate()).allowed
        async with factory() as db:
            await ProviderTrustLifecycleApplicationService(db).apply_professional(
                actor_id=reviewer_id,
                authentication=_auth(reviewer_id, now),
                resource_id=professional_id,
                command=ProfessionalTransitionCommand.SUSPEND,
                facts=ProfessionalTransitionFacts(decision_reason_code="SYNTHETIC"),
                expected_version=1,
                idempotency_key="phase3e-professional-suspend-0001",
                now=now,
            )
        assert (await evaluate()).denial_code is ClinicalEligibilityDenialCode.PROFESSIONAL_SUSPENDED
        async with factory() as db:
            await ProviderTrustLifecycleApplicationService(db).apply_professional(
                actor_id=reviewer_id,
                authentication=_auth(reviewer_id, now),
                resource_id=professional_id,
                command=ProfessionalTransitionCommand.RESTORE,
                facts=_professional_verify_facts(registration_number),
                expected_version=2,
                idempotency_key="phase3e-professional-restore-0001",
                now=now,
            )
            await ProviderTrustLifecycleApplicationService(db).apply_facility(
                actor_id=reviewer_id,
                authentication=_auth(reviewer_id, now),
                resource_id=facility_verification_id,
                command=FacilityTransitionCommand.SUSPEND,
                facts=FacilityTransitionFacts(decision_reason_code="SYNTHETIC"),
                expected_version=1,
                idempotency_key="phase3e-facility-suspend-0001",
                now=now,
            )
        assert (await evaluate()).denial_code is ClinicalEligibilityDenialCode.FACILITY_SUSPENDED
        async with factory() as db:
            await ProviderTrustLifecycleApplicationService(db).apply_facility(
                actor_id=reviewer_id,
                authentication=_auth(reviewer_id, now),
                resource_id=facility_verification_id,
                command=FacilityTransitionCommand.RESTORE,
                facts=FacilityTransitionFacts(
                    verification_method="synthetic-review",
                    verification_source="synthetic-source",
                    verification_reference="synthetic-reference",
                ),
                expected_version=2,
                idempotency_key="phase3e-facility-restore-0001",
                now=now,
            )
            await ProviderTrustLifecycleApplicationService(db).apply_affiliation(
                actor_id=reviewer_id,
                authentication=_auth(reviewer_id, now),
                resource_id=affiliation_id,
                command=AffiliationTransitionCommand.SUSPEND,
                facts=AffiliationTransitionFacts(decision_reason_code="SYNTHETIC"),
                expected_version=1,
                idempotency_key="phase3e-affiliation-suspend-0001",
                now=now,
            )
        assert (await evaluate()).denial_code is ClinicalEligibilityDenialCode.AFFILIATION_SUSPENDED
        async with factory() as db:
            affiliation = await db.get(ProviderHospitalAffiliation, affiliation_id)
            assert affiliation.roles == ["clinician"]
    finally:
        await engine.dispose()
