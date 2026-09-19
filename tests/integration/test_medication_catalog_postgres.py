"""PostgreSQL qualification for Slice 10B.5k medication-catalog authority."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.medication_catalog import (
    DrugScheduleClass,
    MedicationCatalogEntry,
    MedicationCatalogRelease,
    MedicationCatalogReleaseStatus,
    MedicationEmergencyAction,
    MedicationEmergencyReason,
    MedicationEvidenceAuthority,
    MedicationEvidenceDimension,
    NdpsClass,
    NexaHighRiskClass,
    RegulatoryProductStatus,
    SpecialRecordkeepingClass,
    TelemedicineClass,
    TerminologyConceptStatus,
)
from app.models.provider import (
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.medication_catalog_application import (
    DraftMedicationEntryInput,
    DraftMedicationEvidenceInput,
    MedicationCatalogApplicationError,
    MedicationCatalogApplicationService,
)
from app.services.medication_catalog_runtime import (
    MedicationCatalogRuntimeError,
    MedicationCatalogRuntimeService,
)
from app.services.medication_catalog_signing import (
    InMemoryMedicationCatalogTestSigner,
)
from app.services.provider_trust_authorization import TrustManagementAuthentication
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

HEAD = "20260919_medication_catalog"
_DB_NAME = "nexa_qual_medication_catalog"
NOW = datetime(2026, 9, 20, 0, 30, tzinfo=timezone.utc)


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


async def _provider(session_factory, label: str) -> uuid.UUID:
    provider_id = uuid.uuid4()
    async with session_factory() as db:
        db.add_all(
            [
                ProviderIdentity(
                    id=provider_id,
                    provider_uid=f"catalog-{label}-{provider_id.hex[:8]}",
                    status="active",
                    is_active=True,
                    display_name=f"Synthetic Catalog {label}",
                    contact_email=f"{label}-{provider_id.hex[:8]}@example.test",
                    contact_phone=f"+91{provider_id.int % 10_000_000_000:010d}",
                    email_verified_at=NOW - timedelta(days=1),
                    phone_verified_at=NOW - timedelta(days=1),
                ),
                ProviderCredential(
                    id=uuid.uuid4(),
                    provider_id=provider_id,
                    login_identifier=f"{label}-{provider_id.hex[:8]}@example.test",
                    password_hash="synthetic-qualification-only",
                    mfa_enabled=True,
                    is_active=True,
                ),
            ]
        )
        await db.commit()
    return provider_id


async def _grant(session_factory, provider_id: uuid.UUID) -> None:
    async with session_factory() as db:
        db.add(
            ProviderTrustPermissionGrant(
                id=uuid.uuid4(),
                provider_id=provider_id,
                permission=TrustManagementPermission.MEDICATION_CATALOG_RELEASE_REVIEW.value,
                scope_type=TrustPermissionScope.GLOBAL.value,
                facility_id=None,
                granted_at=NOW - timedelta(hours=2),
                valid_from=NOW - timedelta(hours=2),
                valid_until=NOW + timedelta(days=7),
                revoked_at=None,
                granted_by_actor_id="synthetic-catalog-root",
                governance_reference="QUAL-10B5K-SYNTHETIC",
            )
        )
        await db.commit()


def _auth(provider_id: uuid.UUID, *, minutes: int = 2) -> TrustManagementAuthentication:
    return TrustManagementAuthentication(
        provider_id=provider_id,
        method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
        session_authenticated=True,
        mfa_verified_at=NOW - timedelta(minutes=minutes),
    )


def _entry(code: str, *, allowed_shape: bool = True) -> DraftMedicationEntryInput:
    return DraftMedicationEntryInput(
        medication_code=code,
        code_system="SYNTHETIC-TEST-ONLY",
        code_system_version="synthetic-v1",
        canonical_generic_name=f"Synthetic Generic {code}",
        medication_display=f"Synthetic Display {code}",
        ingredient_identity=f"Synthetic Ingredient {code}",
        dose_form="synthetic-form",
        identity_strength_descriptor="synthetic-identity-strength",
        identity_granularity_sufficient=True,
        terminology_status=TerminologyConceptStatus.ACTIVE,
        drug_schedule_class=(
            DrugScheduleClass.NONE_CONFIRMED
            if allowed_shape
            else DrugScheduleClass.H
        ),
        ndps_class=NdpsClass.NOT_CONTROLLED_CONFIRMED,
        telemedicine_class=TelemedicineClass.LIST_O_ANY_MODE,
        special_recordkeeping_class=SpecialRecordkeepingClass.NONE_CONFIRMED,
        nexa_high_risk_class=NexaHighRiskClass.NONE_CONFIRMED,
        regulatory_product_status=RegulatoryProductStatus.CURRENT,
        classification_rationale_code="SYNTHETIC_TEST_ONLY",
    )


def _evidence() -> tuple[DraftMedicationEvidenceInput, ...]:
    values = []
    for index, dimension in enumerate(
        (
            MedicationEvidenceDimension.IDENTITY,
            MedicationEvidenceDimension.DRUG_SCHEDULE,
            MedicationEvidenceDimension.NDPS,
            MedicationEvidenceDimension.TELEMEDICINE,
            MedicationEvidenceDimension.SPECIAL_RECORDKEEPING,
            MedicationEvidenceDimension.HIGH_RISK,
            MedicationEvidenceDimension.REGULATORY_PRODUCT_STATUS,
        ),
        start=1,
    ):
        values.append(
            DraftMedicationEvidenceInput(
                finding_dimension=dimension,
                source_authority=(
                    MedicationEvidenceAuthority.NRCES
                    if dimension is MedicationEvidenceDimension.IDENTITY
                    else MedicationEvidenceAuthority.CDSCO
                ),
                source_document_version="synthetic-source-v1",
                source_reference=f"synthetic://10b5k/{dimension.value}",
                publication_date=None,
                effective_date=None,
                checked_at=NOW - timedelta(hours=1),
                finding_value="SYNTHETIC_CONFIRMED",
                rationale_code="SYNTHETIC_TEST_ONLY",
                evidence_sha256=f"{index:x}" * 64,
            )
        )
    return tuple(values)


async def _create_release_with_entry(
    session_factory,
    *,
    signer,
    preparer: uuid.UUID,
    code: str,
    allowed_shape: bool,
):
    async with session_factory() as db:
        service = MedicationCatalogApplicationService(db, signing_provider=signer)
        created = await service.create_release(
            actor_id=preparer,
            authentication=_auth(preparer),
            version=f"synthetic-{code.lower()}-{uuid.uuid4().hex[:8]}",
            source_cutoff_at=NOW,
            source_terminology_version="synthetic-terminology-v1",
            idempotency_key=f"create-{uuid.uuid4().hex}",
            now=NOW,
        )
    async with session_factory() as db:
        service = MedicationCatalogApplicationService(db, signing_provider=signer)
        entry = await service.upsert_draft_entry(
            actor_id=preparer,
            authentication=_auth(preparer),
            release_id=created.resource_id,
            entry_input=_entry(code, allowed_shape=allowed_shape),
            idempotency_key=f"entry-{uuid.uuid4().hex}",
            now=NOW,
        )
    async with session_factory() as db:
        service = MedicationCatalogApplicationService(db, signing_provider=signer)
        await service.replace_draft_evidence(
            actor_id=preparer,
            authentication=_auth(preparer),
            release_id=created.resource_id,
            entry_id=entry.resource_id,
            evidence=_evidence(),
            idempotency_key=f"evidence-{uuid.uuid4().hex}",
            now=NOW,
        )
    return created.resource_id, entry.resource_id


async def test_full_catalog_authority_lifecycle_runtime_and_emergency_overlay(session_factory):
    signer = InMemoryMedicationCatalogTestSigner()
    preparer = await _provider(session_factory, "preparer")
    reviewer_one = await _provider(session_factory, "reviewer-one")
    reviewer_two = await _provider(session_factory, "reviewer-two")
    unauthorized = await _provider(session_factory, "clinician-only")
    for actor in (preparer, reviewer_one, reviewer_two):
        await _grant(session_factory, actor)

    release_id, entry_id = await _create_release_with_entry(
        session_factory,
        signer=signer,
        preparer=preparer,
        code="SYNTH-ALLOW-001",
        allowed_shape=True,
    )

    async with session_factory() as db:
        with pytest.raises(MedicationCatalogApplicationError) as no_permission:
            await MedicationCatalogApplicationService(
                db, signing_provider=signer
            ).review_positive_entry(
                actor_id=unauthorized,
                authentication=_auth(unauthorized),
                release_id=release_id,
                entry_id=entry_id,
                idempotency_key=f"unauthorized-{uuid.uuid4().hex}",
                now=NOW,
            )
    assert no_permission.value.code == "AUTHORIZATION_DENIED"

    async with session_factory() as db:
        with pytest.raises(MedicationCatalogApplicationError) as self_review:
            await MedicationCatalogApplicationService(
                db, signing_provider=signer
            ).review_positive_entry(
                actor_id=preparer,
                authentication=_auth(preparer),
                release_id=release_id,
                entry_id=entry_id,
                idempotency_key=f"self-{uuid.uuid4().hex}",
                now=NOW,
            )
    assert self_review.value.code == "SELF_REVIEW_PROHIBITED"

    for actor in (reviewer_one, reviewer_two):
        async with session_factory() as db:
            await MedicationCatalogApplicationService(
                db, signing_provider=signer
            ).review_positive_entry(
                actor_id=actor,
                authentication=_auth(actor),
                release_id=release_id,
                entry_id=entry_id,
                idempotency_key=f"review-{actor}-{uuid.uuid4().hex}",
                now=NOW,
            )

    async with session_factory() as db:
        qualified = await MedicationCatalogApplicationService(
            db, signing_provider=signer
        ).qualify_release(
            actor_id=reviewer_one,
            authentication=_auth(reviewer_one),
            release_id=release_id,
            idempotency_key=f"qualify-{uuid.uuid4().hex}",
            now=NOW,
        )
    assert qualified.status == MedicationCatalogReleaseStatus.QUALIFIED.value

    async with session_factory() as db:
        activated = await MedicationCatalogApplicationService(
            db, signing_provider=signer
        ).activate_release(
            actor_id=reviewer_two,
            authentication=_auth(reviewer_two),
            release_id=release_id,
            idempotency_key=f"activate-{uuid.uuid4().hex}",
            now=NOW,
        )
    assert activated.status == MedicationCatalogReleaseStatus.ACTIVE.value

    async with session_factory() as db:
        resolved = await MedicationCatalogRuntimeService(
            db, signing_provider=signer
        ).resolve_v1_allowed_medication("SYNTH-ALLOW-001", now=NOW)
        assert resolved.release_id == release_id
        assert resolved.medication_code == "SYNTH-ALLOW-001"
        assert resolved.medication_display == "Synthetic Display SYNTH-ALLOW-001"

    async with session_factory() as db:
        with pytest.raises(DBAPIError) as published_entry_update:
            await db.execute(
                text(
                    "UPDATE medication_catalog_entry "
                    "SET medication_display = 'tampered' WHERE id = :id"
                ),
                {"id": entry_id},
            )
            await db.commit()
    assert "MEDICATION_CATALOG_PUBLISHED_CONTENT_IMMUTABLE" in str(
        published_entry_update.value
    )

    async with session_factory() as db:
        await MedicationCatalogApplicationService(
            db, signing_provider=signer
        ).append_emergency_policy(
            actor_id=reviewer_one,
            authentication=_auth(reviewer_one),
            medication_code="SYNTH-ALLOW-001",
            action=MedicationEmergencyAction.DENY,
            reason_code=MedicationEmergencyReason.PATIENT_SAFETY_HOLD,
            evidence_reference="synthetic://emergency/deny",
            evidence_sha256="d" * 64,
            idempotency_key=f"deny-{uuid.uuid4().hex}",
            effective_at=NOW,
            now=NOW,
        )

    async with session_factory() as db:
        with pytest.raises(MedicationCatalogRuntimeError) as denied:
            await MedicationCatalogRuntimeService(
                db, signing_provider=signer
            ).resolve_v1_allowed_medication("SYNTH-ALLOW-001", now=NOW)
    assert denied.value.code == "CATALOG_EMERGENCY_DENY"

    async with session_factory() as db:
        await MedicationCatalogApplicationService(
            db, signing_provider=signer
        ).append_emergency_policy(
            actor_id=reviewer_two,
            authentication=_auth(reviewer_two),
            medication_code="SYNTH-ALLOW-001",
            action=MedicationEmergencyAction.CLEAR,
            reason_code=MedicationEmergencyReason.PATIENT_SAFETY_HOLD,
            evidence_reference="synthetic://emergency/clear",
            evidence_sha256="e" * 64,
            idempotency_key=f"clear-{uuid.uuid4().hex}",
            effective_at=NOW,
            now=NOW,
        )

    async with session_factory() as db:
        resolved = await MedicationCatalogRuntimeService(
            db, signing_provider=signer
        ).resolve_v1_allowed_medication("SYNTH-ALLOW-001", now=NOW)
        assert resolved.medication_code == "SYNTH-ALLOW-001"


async def test_draft_change_invalidates_reviews_and_stale_mfa_fails(session_factory):
    signer = InMemoryMedicationCatalogTestSigner()
    preparer = await _provider(session_factory, "change-preparer")
    reviewer_one = await _provider(session_factory, "change-reviewer-one")
    reviewer_two = await _provider(session_factory, "change-reviewer-two")
    for actor in (preparer, reviewer_one, reviewer_two):
        await _grant(session_factory, actor)

    release_id, entry_id = await _create_release_with_entry(
        session_factory,
        signer=signer,
        preparer=preparer,
        code="SYNTH-CHANGE-001",
        allowed_shape=True,
    )

    async with session_factory() as db:
        with pytest.raises(MedicationCatalogApplicationError) as stale:
            await MedicationCatalogApplicationService(
                db, signing_provider=signer
            ).review_positive_entry(
                actor_id=reviewer_one,
                authentication=_auth(reviewer_one, minutes=16),
                release_id=release_id,
                entry_id=entry_id,
                idempotency_key=f"stale-{uuid.uuid4().hex}",
                now=NOW,
            )
    assert stale.value.code == "MFA_STEP_UP_REQUIRED"

    for actor in (reviewer_one, reviewer_two):
        async with session_factory() as db:
            await MedicationCatalogApplicationService(
                db, signing_provider=signer
            ).review_positive_entry(
                actor_id=actor,
                authentication=_auth(actor),
                release_id=release_id,
                entry_id=entry_id,
                idempotency_key=f"review-change-{uuid.uuid4().hex}",
                now=NOW,
            )

    changed = _entry("SYNTH-CHANGE-001", allowed_shape=True)
    changed = DraftMedicationEntryInput(
        **{
            **changed.__dict__,
            "medication_display": "Synthetic Display CHANGED",
        }
    )
    async with session_factory() as db:
        await MedicationCatalogApplicationService(
            db, signing_provider=signer
        ).upsert_draft_entry(
            actor_id=preparer,
            authentication=_auth(preparer),
            release_id=release_id,
            entry_input=changed,
            idempotency_key=f"change-{uuid.uuid4().hex}",
            now=NOW,
        )

    async with session_factory() as db:
        row = await db.get(MedicationCatalogEntry, entry_id)
        assert row is not None
        assert row.first_reviewer_provider_id is None
        assert row.second_reviewer_provider_id is None
        assert row.first_review_digest is None
        assert row.second_review_digest is None

    async with session_factory() as db:
        with pytest.raises(MedicationCatalogApplicationError) as qualify:
            await MedicationCatalogApplicationService(
                db, signing_provider=signer
            ).qualify_release(
                actor_id=reviewer_one,
                authentication=_auth(reviewer_one),
                release_id=release_id,
                idempotency_key=f"qualify-stale-{uuid.uuid4().hex}",
                now=NOW,
            )
    assert qualify.value.code == "POSITIVE_REVIEWS_REQUIRED"


async def test_concurrent_activation_keeps_at_most_one_active_release(session_factory):
    signer = InMemoryMedicationCatalogTestSigner()
    preparer_a = await _provider(session_factory, "race-preparer-a")
    preparer_b = await _provider(session_factory, "race-preparer-b")
    reviewer_one = await _provider(session_factory, "race-reviewer-one")
    reviewer_two = await _provider(session_factory, "race-reviewer-two")
    for actor in (preparer_a, preparer_b, reviewer_one, reviewer_two):
        await _grant(session_factory, actor)

    async def prepared(preparer, code):
        release_id, entry_id = await _create_release_with_entry(
            session_factory,
            signer=signer,
            preparer=preparer,
            code=code,
            allowed_shape=True,
        )
        for actor in (reviewer_one, reviewer_two):
            async with session_factory() as db:
                await MedicationCatalogApplicationService(
                    db, signing_provider=signer
                ).review_positive_entry(
                    actor_id=actor,
                    authentication=_auth(actor),
                    release_id=release_id,
                    entry_id=entry_id,
                    idempotency_key=f"race-review-{uuid.uuid4().hex}",
                    now=NOW,
                )
        async with session_factory() as db:
            await MedicationCatalogApplicationService(
                db, signing_provider=signer
            ).qualify_release(
                actor_id=reviewer_one,
                authentication=_auth(reviewer_one),
                release_id=release_id,
                idempotency_key=f"race-qualify-{uuid.uuid4().hex}",
                now=NOW,
            )
        return release_id

    release_a = await prepared(preparer_a, "SYNTH-RACE-A")
    release_b = await prepared(preparer_b, "SYNTH-RACE-B")

    async def activate(release_id):
        async with session_factory() as db:
            try:
                result = await MedicationCatalogApplicationService(
                    db, signing_provider=signer
                ).activate_release(
                    actor_id=reviewer_two,
                    authentication=_auth(reviewer_two),
                    release_id=release_id,
                    idempotency_key=f"race-activate-{release_id}",
                    now=NOW,
                )
                return ("success", result.resource_id)
            except Exception as exc:
                return ("error", type(exc).__name__)

    await asyncio.gather(activate(release_a), activate(release_b))

    async with session_factory() as db:
        active = list(
            (
                await db.execute(
                    select(MedicationCatalogRelease).where(
                        MedicationCatalogRelease.status
                        == MedicationCatalogReleaseStatus.ACTIVE.value
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(active) <= 1


async def test_zero_active_and_unknown_code_fail_closed(session_factory):
    signer = InMemoryMedicationCatalogTestSigner()
    async with session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(MedicationCatalogRelease).where(
                        MedicationCatalogRelease.status
                        == MedicationCatalogReleaseStatus.ACTIVE.value
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await db.execute(
                text(
                    "UPDATE medication_catalog_release "
                    "SET status='REVOKED', revoked_at=:now WHERE id=:id"
                ),
                {"now": NOW, "id": row.id},
            )
        await db.commit()

    async with session_factory() as db:
        with pytest.raises(MedicationCatalogRuntimeError) as unavailable:
            await MedicationCatalogRuntimeService(
                db, signing_provider=signer
            ).resolve_v1_allowed_medication("SYNTH-UNKNOWN", now=NOW)
    assert unavailable.value.code == "CATALOG_UNAVAILABLE"
