"""Disposable PostgreSQL qualification for registration-recovery manual review."""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_registration_recovery_review import (
    PatientRegistrationRecoveryReviewCase,
    PatientRegistrationRecoveryReviewDisposition,
    RegistrationRecoveryReviewOutcome,
    RegistrationRecoveryReviewReason,
    RegistrationRecoveryReviewStatus,
)
from app.services.patient_registration_recovery_review_service import (
    REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED,
    PatientRegistrationRecoveryReviewError,
    claim_reviewer_case,
    open_registration_recovery_review_case,
    patient_review_status,
    read_reviewer_case,
    resolve_reviewer_case,
)
from app.services.patient_registration_recovery_service import (
    inspect_patient_registration_recovery,
)

pytestmark = pytest.mark.postgres


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    normalized = value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "127.0.0.1" not in normalized and "localhost" not in normalized:
        pytest.fail("TEST_DATABASE_URL must be loopback-only")
    if "nexa_qual_" not in normalized:
        pytest.fail("TEST_DATABASE_URL must name a disposable nexa_qual_ database")
    return normalized


def _reviewer(name: str, binding: str):
    return SimpleNamespace(
        reviewer_id=name,
        authority_version="registration-recovery-review-auth/1.0",
        session_binding=binding,
    )


async def _seed_manual_case(factory, *, suffix: str):
    patient_id = uuid.uuid4()
    subject = f"slice9-review-{suffix}-{uuid.uuid4().hex}"
    attempt_id = uuid.uuid4().hex
    async with factory() as db:
        db.add(Patient(patient_uuid=patient_id))
        db.add(
            PatientAuthIdentity(
                patient_id=patient_id,
                provider="supabase",
                provider_subject=subject,
                revoked_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            )
        )
        await db.commit()
    async with factory() as db:
        inspection = await inspect_patient_registration_recovery(
            db, provider_subject=subject
        )
        assert inspection.disposition == "manual_review"
        assert inspection.reason_code == "IDENTITY_REVOKED"
        case = await open_registration_recovery_review_case(
            db, inspection=inspection, attempt_id=attempt_id
        )
        await db.commit()
        return patient_id, subject, case.case_reference, case.id


async def _cleanup(factory, *, patient_id: uuid.UUID, case_id: uuid.UUID) -> None:
    async with factory() as db:
        await db.execute(
            text(
                "DELETE FROM public.audit_outbox "
                "WHERE payload->>'target_id' IN (SELECT case_reference FROM "
                "patient_registration_recovery_review_cases WHERE id = :case_id)"
            ),
            {"case_id": case_id},
        )
        await db.execute(
            delete(PatientRegistrationRecoveryReviewDisposition).where(
                PatientRegistrationRecoveryReviewDisposition.case_id == case_id
            )
        )
        await db.execute(
            delete(PatientRegistrationRecoveryReviewCase).where(
                PatientRegistrationRecoveryReviewCase.id == case_id
            )
        )
        await db.execute(
            delete(PatientAuthIdentity).where(
                PatientAuthIdentity.patient_id == patient_id
            )
        )
        await db.execute(delete(Patient).where(Patient.patient_uuid == patient_id))
        await db.commit()


@pytest.mark.asyncio
async def test_claim_race_reviewer_isolation_and_terminal_replay_are_linearizable() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    patient_id, _, case_reference, case_id = await _seed_manual_case(
        factory, suffix="race"
    )
    reviewer_a = _reviewer("reviewer-a", "a" * 64)
    reviewer_b = _reviewer("reviewer-b", "b" * 64)
    try:
        async def claim_once(reviewer):
            async with factory() as db:
                try:
                    case = await claim_reviewer_case(
                        db,
                        case_reference=case_reference,
                        reviewer=reviewer,
                        expected_version=1,
                    )
                    await db.commit()
                    return ("ok", reviewer, case.version)
                except PatientRegistrationRecoveryReviewError as exc:
                    await db.rollback()
                    return (exc.code, reviewer, None)

        first, second = await asyncio.gather(
            claim_once(reviewer_a), claim_once(reviewer_b)
        )
        winners = [row for row in (first, second) if row[0] == "ok"]
        losers = [row for row in (first, second) if row[0] != "ok"]
        assert len(winners) == 1
        assert len(losers) == 1
        winner = winners[0][1]
        loser = losers[0][1]
        assert winners[0][2] == 2

        async with factory() as db:
            with pytest.raises(PatientRegistrationRecoveryReviewError) as denied:
                await read_reviewer_case(
                    db, case_reference=case_reference, reviewer=loser
                )
            assert denied.value.code == REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED
            await db.rollback()

        idempotency_key = f"slice9-terminal-{uuid.uuid4().hex}"

        async def resolve_once():
            async with factory() as db:
                case, disposition = await resolve_reviewer_case(
                    db,
                    case_reference=case_reference,
                    reviewer=winner,
                    expected_version=2,
                    idempotency_key=idempotency_key,
                    outcome=RegistrationRecoveryReviewOutcome.NO_REPAIR,
                    reason_codes=[RegistrationRecoveryReviewReason.IDENTITY_REVOKED],
                )
                disposition_id = disposition.id
                await db.commit()
                return case.status, case.version, disposition_id

        resolved_first, resolved_second = await asyncio.gather(
            resolve_once(), resolve_once()
        )
        assert resolved_first[0] == RegistrationRecoveryReviewStatus.REJECTED.value
        assert resolved_second[0] == RegistrationRecoveryReviewStatus.REJECTED.value
        assert resolved_first[1] == resolved_second[1] == 3
        assert resolved_first[2] == resolved_second[2]

        async with factory() as db:
            dispositions = list(
                (
                    await db.scalars(
                        select(PatientRegistrationRecoveryReviewDisposition).where(
                            PatientRegistrationRecoveryReviewDisposition.case_id
                            == case_id
                        )
                    )
                ).all()
            )
            assert len(dispositions) == 1
            rejected_audits = int(
                await db.scalar(
                    text(
                        "SELECT count(*) FROM public.audit_outbox "
                        "WHERE payload->>'target_id' = :target AND "
                        "event_type = 'PATIENT_REGISTRATION_RECOVERY_REVIEW_REJECTED'"
                    ),
                    {"target": case_reference},
                )
                or 0
            )
            assert rejected_audits == 1
            public_status = await patient_review_status(
                db, case_reference=case_reference
            )
            assert set(public_status) == {
                "case_reference",
                "status",
                "terminal",
                "next_action",
                "created_at",
                "resolved_at",
            }
            assert public_status["terminal"] is True
            assert public_status["next_action"] == "CONTACT_SUPPORT"
    finally:
        await _cleanup(factory, patient_id=patient_id, case_id=case_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_audit_failure_rolls_back_disposition_and_case_transition() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    patient_id, _, case_reference, case_id = await _seed_manual_case(
        factory, suffix="rollback"
    )
    reviewer = _reviewer("reviewer-rollback", "c" * 64)
    try:
        async with factory() as db:
            await claim_reviewer_case(
                db,
                case_reference=case_reference,
                reviewer=reviewer,
                expected_version=1,
            )
            await db.commit()

        async with factory() as db:
            with patch(
                "app.services.patient_registration_recovery_review_service.enqueue_audit_event",
                new=AsyncMock(side_effect=RuntimeError("synthetic audit failure")),
            ):
                with pytest.raises(RuntimeError, match="synthetic audit failure"):
                    await resolve_reviewer_case(
                        db,
                        case_reference=case_reference,
                        reviewer=reviewer,
                        expected_version=2,
                        idempotency_key=f"slice9-rollback-{uuid.uuid4().hex}",
                        outcome=RegistrationRecoveryReviewOutcome.NO_REPAIR,
                        reason_codes=[
                            RegistrationRecoveryReviewReason.IDENTITY_REVOKED
                        ],
                    )
                await db.rollback()

        async with factory() as db:
            case = await db.get(PatientRegistrationRecoveryReviewCase, case_id)
            assert case is not None
            assert case.status == RegistrationRecoveryReviewStatus.IN_REVIEW.value
            assert case.version == 2
            disposition = await db.scalar(
                select(PatientRegistrationRecoveryReviewDisposition).where(
                    PatientRegistrationRecoveryReviewDisposition.case_id == case_id
                )
            )
            assert disposition is None
    finally:
        await _cleanup(factory, patient_id=patient_id, case_id=case_id)
        await engine.dispose()
