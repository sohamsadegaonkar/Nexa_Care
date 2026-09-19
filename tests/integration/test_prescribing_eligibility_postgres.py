"""PostgreSQL qualification for Slice 10B.5h prescribing authority."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.provider import (
    PrescribingEligibilityDecision,
    PrescribingEligibilityReasonCode,
    PrescribingEligibilitySourceType,
    PrescribingEligibilityStatus,
    PrescribingPractitionerClass,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.prescribing_eligibility_application import (
    PrescribingEligibilityApplicationError,
    PrescribingEligibilityApplicationService,
)
from app.services.provider_trust_authorization import TrustManagementAuthentication
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.asyncio,
]

HEAD = "20260919_prescriber_eligibility"
_DB_NAME = "nexa_qual_prescriber_eligibility"
NOW = datetime(2026, 9, 19, 12, 45, tzinfo=timezone.utc)


def _db_url() -> str:
    return postgres_database_url(_DB_NAME)


@pytest.fixture(scope="module", autouse=True)
def _database():
    url = _db_url()
    asyncio.run(create_disposable_database(_DB_NAME))
    migrate_database_to_head(url, target_head=HEAD)
    yield
    asyncio.run(drop_disposable_database(_DB_NAME))


@pytest.fixture
def session_factory():
    engine = create_async_engine(_db_url(), echo=False)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_provider(
    session_factory,
    *,
    email: str,
    professional: bool,
) -> tuple[uuid.UUID, ProfessionalVerification | None]:
    provider_id = uuid.uuid4()
    async with session_factory() as db:
        provider = ProviderIdentity(
            id=provider_id,
            provider_uid=f"qual-{provider_id.hex[:12]}",
            status="active",
            is_active=True,
            display_name="Qualification Provider",
            contact_email=email,
            contact_phone=f"+91{provider_id.int % 10_000_000_000:010d}",
            email_verified_at=NOW - timedelta(days=2),
            phone_verified_at=NOW - timedelta(days=2),
        )
        credential = ProviderCredential(
            id=uuid.uuid4(),
            provider_id=provider_id,
            login_identifier=email,
            password_hash="qualification-only",
            mfa_enabled=True,
            is_active=True,
        )
        db.add_all([provider, credential])
        verification = None
        if professional:
            verification = ProfessionalVerification(
                id=uuid.uuid4(),
                provider_id=provider_id,
                status=ProfessionalVerificationStatus.VERIFIED.value,
                registration_authority_code="NMC",
                registration_number_normalized=f"NMC-{provider_id.hex[:16]}",
                verification_method="HUMAN_PRIMARY_SOURCE_REVIEW",
                verification_source="NMR",
                verification_reference=f"NMR-{provider_id.hex[:12]}",
                identity_binding_method="PRIMARY_REGISTER_MATCH",
                identity_binding_status="MATCHED",
                registration_valid_from=NOW - timedelta(days=120),
                registration_valid_until=NOW + timedelta(days=180),
                verified_at=NOW - timedelta(days=1),
                last_checked_at=NOW - timedelta(days=1),
                next_review_at=NOW + timedelta(days=12),
                authoritative_adverse_signal_at=None,
                previous_verification_valid=True,
                version=4,
            )
            db.add(verification)
        await db.commit()
    return provider_id, verification


async def _seed_reviewer_permission(session_factory, reviewer_id: uuid.UUID) -> None:
    async with session_factory() as db:
        db.add(
            ProviderTrustPermissionGrant(
                id=uuid.uuid4(),
                provider_id=reviewer_id,
                permission=(
                    TrustManagementPermission.PRESCRIBING_ELIGIBILITY_REVIEW.value
                ),
                scope_type=TrustPermissionScope.GLOBAL.value,
                facility_id=None,
                granted_at=NOW - timedelta(days=1),
                valid_from=NOW - timedelta(days=1),
                valid_until=NOW + timedelta(days=30),
                revoked_at=None,
                granted_by_actor_id="qualification-root",
                governance_reference="QUAL-10B5H",
            )
        )
        await db.commit()


def _auth(provider_id: uuid.UUID) -> TrustManagementAuthentication:
    return TrustManagementAuthentication(
        provider_id=provider_id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=NOW - timedelta(minutes=2),
    )


def _kwargs(
    *,
    reviewer_id: uuid.UUID,
    target_id: uuid.UUID,
    professional_version: int,
    previous_version: int,
    key: str,
    source_reference: str = "NMR:QUALIFIED-CURRENT",
):
    return {
        "actor_id": reviewer_id,
        "authentication": _auth(reviewer_id),
        "target_provider_id": target_id,
        "expected_professional_verification_version": professional_version,
        "expected_previous_decision_version": previous_version,
        "status": PrescribingEligibilityStatus.ELIGIBLE,
        "practitioner_class": (
            PrescribingPractitionerClass.FULL_RMP_MODERN_MEDICINE
        ),
        "source_type": PrescribingEligibilitySourceType.NMR,
        "source_reference": source_reference,
        "evidence_sha256": "c" * 64,
        "decision_reason_code": (
            PrescribingEligibilityReasonCode.PRIMARY_SOURCE_CURRENT_FULL_RMP
        ),
        "restriction_code": None,
        "idempotency_key": key,
        "now": NOW,
    }


async def test_prescribing_eligibility_authority_is_append_only_atomic_and_bounded(
    session_factory,
):
    reviewer_id, _ = await _seed_provider(
        session_factory,
        email=f"reviewer-{uuid.uuid4().hex[:8]}@example.test",
        professional=False,
    )
    target_id, professional = await _seed_provider(
        session_factory,
        email=f"target-{uuid.uuid4().hex[:8]}@example.test",
        professional=True,
    )
    assert professional is not None
    await _seed_reviewer_permission(session_factory, reviewer_id)

    key = f"qual-prescribing-{uuid.uuid4().hex}"
    async with session_factory() as db:
        result = await PrescribingEligibilityApplicationService(db).apply_decision(
            **_kwargs(
                reviewer_id=reviewer_id,
                target_id=target_id,
                professional_version=professional.version,
                previous_version=0,
                key=key,
            )
        )

    assert result.version == 1
    assert result.status == PrescribingEligibilityStatus.ELIGIBLE.value
    assert result.valid_until == NOW + timedelta(days=12)
    assert result.valid_until <= NOW + timedelta(days=30)

    async with session_factory() as db:
        row = await db.get(PrescribingEligibilityDecision, result.decision_id)
        assert row is not None
        assert row.provider_id == target_id
        assert row.professional_verification_id == professional.id
        assert row.professional_verification_version == professional.version
        assert row.registration_authority_code == "NMC"
        assert row.registration_number_normalized == professional.registration_number_normalized
        assert row.reviewer_provider_id == reviewer_id

        outbox_payload = await db.scalar(
            text(
                "SELECT payload::text FROM public.audit_outbox "
                "WHERE event_type = 'PRESCRIBING_ELIGIBILITY_DECISION_RECORDED' "
                "AND target_id = :target_id"
            ),
            {"target_id": str(result.decision_id)},
        )
        assert outbox_payload is not None
        assert "NMR:QUALIFIED-CURRENT" not in outbox_payload
        assert professional.registration_number_normalized not in outbox_payload
        assert "c" * 64 not in outbox_payload

    async with session_factory() as db:
        replay = await PrescribingEligibilityApplicationService(db).apply_decision(
            **_kwargs(
                reviewer_id=reviewer_id,
                target_id=target_id,
                professional_version=professional.version,
                previous_version=0,
                key=key,
            )
        )
    assert replay.decision_id == result.decision_id
    assert replay.idempotent_replay is True

    async with session_factory() as db:
        with pytest.raises(PrescribingEligibilityApplicationError) as conflict:
            await PrescribingEligibilityApplicationService(db).apply_decision(
                **_kwargs(
                    reviewer_id=reviewer_id,
                    target_id=target_id,
                    professional_version=professional.version,
                    previous_version=0,
                    key=key,
                    source_reference="NMR:CHANGED",
                )
            )
    assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"

    async with session_factory() as db:
        with pytest.raises(DBAPIError) as immutable_update:
            await db.execute(
                text(
                    "UPDATE prescribing_eligibility_decision "
                    "SET status = 'REVOKED' WHERE id = :id"
                ),
                {"id": result.decision_id},
            )
            await db.commit()
        assert "PRESCRIBING_ELIGIBILITY_DECISION_IMMUTABLE" in str(
            immutable_update.value
        )

    async with session_factory() as db:
        with pytest.raises(DBAPIError) as immutable_delete:
            await db.execute(
                text(
                    "DELETE FROM prescribing_eligibility_decision WHERE id = :id"
                ),
                {"id": result.decision_id},
            )
            await db.commit()
        assert "PRESCRIBING_ELIGIBILITY_DECISION_IMMUTABLE" in str(
            immutable_delete.value
        )


async def test_same_previous_version_review_race_has_one_winner(session_factory):
    reviewer_id, _ = await _seed_provider(
        session_factory,
        email=f"reviewer-race-{uuid.uuid4().hex[:8]}@example.test",
        professional=False,
    )
    target_id, professional = await _seed_provider(
        session_factory,
        email=f"target-race-{uuid.uuid4().hex[:8]}@example.test",
        professional=True,
    )
    assert professional is not None
    await _seed_reviewer_permission(session_factory, reviewer_id)

    async def attempt(label: str):
        async with session_factory() as db:
            try:
                result = await PrescribingEligibilityApplicationService(
                    db
                ).apply_decision(
                    **_kwargs(
                        reviewer_id=reviewer_id,
                        target_id=target_id,
                        professional_version=professional.version,
                        previous_version=0,
                        key=f"race-{label}-{uuid.uuid4().hex}",
                    )
                )
                return ("success", result.version)
            except PrescribingEligibilityApplicationError as exc:
                return ("error", exc.code)

    results = await asyncio.gather(attempt("a"), attempt("b"))
    assert sum(item[0] == "success" for item in results) == 1
    assert sum(
        item == ("error", "DECISION_VERSION_CONFLICT") for item in results
    ) == 1

    async with session_factory() as db:
        versions = list(
            (
                await db.execute(
                    select(PrescribingEligibilityDecision.version).where(
                        PrescribingEligibilityDecision.provider_id == target_id
                    )
                )
            ).scalars()
        )
    assert versions == [1]


async def test_self_review_and_hpr_positive_are_denied(session_factory):
    target_id, professional = await _seed_provider(
        session_factory,
        email=f"self-{uuid.uuid4().hex[:8]}@example.test",
        professional=True,
    )
    assert professional is not None

    async with session_factory() as db:
        with pytest.raises(PrescribingEligibilityApplicationError) as self_review:
            await PrescribingEligibilityApplicationService(db).apply_decision(
                **_kwargs(
                    reviewer_id=target_id,
                    target_id=target_id,
                    professional_version=professional.version,
                    previous_version=0,
                    key=f"self-{uuid.uuid4().hex}",
                )
            )
    assert self_review.value.code == "AUTHORIZATION_DENIED"

    reviewer_id, _ = await _seed_provider(
        session_factory,
        email=f"hpr-reviewer-{uuid.uuid4().hex[:8]}@example.test",
        professional=False,
    )
    await _seed_reviewer_permission(session_factory, reviewer_id)
    kwargs = _kwargs(
        reviewer_id=reviewer_id,
        target_id=target_id,
        professional_version=professional.version,
        previous_version=0,
        key=f"hpr-{uuid.uuid4().hex}",
    )
    kwargs["source_type"] = PrescribingEligibilitySourceType.HPR
    async with session_factory() as db:
        with pytest.raises(PrescribingEligibilityApplicationError) as hpr:
            await PrescribingEligibilityApplicationService(db).apply_decision(
                **kwargs
            )
    assert hpr.value.code == "DECISION_POLICY_DENIED"
