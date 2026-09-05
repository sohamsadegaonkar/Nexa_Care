"""Disposable PostgreSQL and Redis contracts for Phase 1B.2 discovery."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi import BackgroundTasks, HTTPException
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock

import app.api.v2.consent_routes as consent_routes
from app.models.erasure_tombstone import (
    ErasureStatus,
    PatientErasureTombstone,
)
from app.models.patient import Patient
from app.models.patient_tombstone import PatientTombstone
from app.services.patient_discovery_service import (
    DISCOVERY_HANDLE_TTL_SECONDS,
    DiscoveryHandleInvalid,
    DiscoveryNoMatch,
    DiscoveryUnavailable,
    PatientDiscoveryService,
    PUBLIC_ID_RE,
    _handle_key,
)
from app.core.rate_limiter import atomic_fixed_window

from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    get_qualification_redis_url,
    normalize_sync_postgres_url,
    postgres_database_url,
)

pytestmark = [pytest.mark.postgres, pytest.mark.redis]

PREVIOUS_HEAD = "20260819_patient_profile_legal"
CURRENT_HEAD = "20260830_delegated_assurance"
_DB_NAME = "nexa_qual_patient_discovery"


def _database_url() -> str:
    return postgres_database_url(_DB_NAME)


def _redis_url() -> str:
    return get_qualification_redis_url()


@pytest.fixture(scope="module", autouse=True)
def _setup_database():
    """Ensure disposable database exists for migration qualification."""
    db_url = _database_url()
    _prev = os.environ.get("TEST_DATABASE_URL")
    os.environ["TEST_DATABASE_URL"] = db_url

    asyncio.run(create_disposable_database(_DB_NAME))
    yield

    if _prev is None:
        os.environ.pop("TEST_DATABASE_URL", None)
    else:
        os.environ["TEST_DATABASE_URL"] = _prev

    asyncio.run(drop_disposable_database(_DB_NAME))


async def _upgrade(url: str, revision: str) -> None:
    cfg = Config("alembic.ini")
    sync_url = normalize_sync_postgres_url(url)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    await asyncio.to_thread(command.upgrade, cfg, revision)


async def _downgrade(url: str, revision: str) -> None:
    cfg = Config("alembic.ini")
    sync_url = normalize_sync_postgres_url(url)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    await asyncio.to_thread(command.downgrade, cfg, revision)


@pytest.mark.asyncio
async def test_public_id_migration_and_discovery_contracts(monkeypatch) -> None:
    """Exercise real PostgreSQL migration, concurrency, merge, deletion, and erasure."""
    url = _database_url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await _upgrade(url, PREVIOUS_HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seeded = [uuid.uuid4() for _ in range(20)]
    try:
        async with engine.begin() as conn:
            for patient_id in seeded:
                await conn.execute(
                    text(
                        "INSERT INTO public.patients (patient_uuid, is_deleted) "
                        "VALUES (:patient_uuid, false)"
                    ),
                    {"patient_uuid": patient_id},
                )
        await _upgrade(url, CURRENT_HEAD)
        async with engine.connect() as conn:
            counts = (
                await conn.execute(
                    text(
                        "SELECT count(*), count(public_patient_id), "
                        "count(DISTINCT public_patient_id), "
                        "count(*) FILTER (WHERE public_patient_id IS NULL), "
                        "count(*) FILTER (WHERE public_patient_id = patient_uuid::text) "
                        "FROM public.patients"
                    )
                )
            ).one()
            values = list(
                (
                    await conn.execute(
                        text("SELECT public_patient_id FROM public.patients")
                    )
                ).scalars()
            )
        assert counts == (20, 20, 20, 0, 0)
        assert all(PUBLIC_ID_RE.fullmatch(value) for value in values)

        async def create_patient() -> str:
            async with factory() as db:
                patient = Patient(is_deleted=False)
                db.add(patient)
                await db.commit()
                return patient.public_patient_id

        created = await asyncio.gather(*(create_patient() for _ in range(16)))
        assert len(created) == len(set(created)) == 16
        assert all(PUBLIC_ID_RE.fullmatch(value) for value in created)

        old_id, canonical_id, deleted_id, erased_id = (uuid.uuid4() for _ in range(4))
        async with factory() as db:
            old = Patient(patient_uuid=old_id, is_deleted=False)
            canonical = Patient(patient_uuid=canonical_id, is_deleted=False)
            deleted = Patient(patient_uuid=deleted_id, is_deleted=True)
            erased = Patient(patient_uuid=erased_id, is_deleted=False)
            db.add_all([old, canonical, deleted, erased])
            await db.flush()
            db.add(
                PatientTombstone(
                    old_patient_uuid=old_id,
                    canonical_patient_uuid=canonical_id,
                    merged_by="qualification",
                )
            )
            db.add(
                PatientErasureTombstone(
                    patient_ref=str(erased_id),
                    status=ErasureStatus.REQUESTED.value,
                    assurance_level="active_access_blocked",
                    wrapping_key_type="shared",
                )
            )
            await db.commit()
            service = PatientDiscoveryService(db, redis=None)
            merged, redirected = await service.resolve_public_id(old.public_patient_id)
            assert merged.patient_uuid == canonical_id and redirected is True
            with pytest.raises(DiscoveryNoMatch):
                await service.resolve_public_id(deleted.public_patient_id)
            with pytest.raises(DiscoveryNoMatch):
                await service.resolve_public_id(erased.public_patient_id)

        async with engine.connect() as conn:
            issued_ids_before_downgrade = set(
                (
                    await conn.execute(
                        text("SELECT public_patient_id FROM public.patients")
                    )
                ).scalars()
            )

        with pytest.raises(RuntimeError, match="forward-only"):
            await _downgrade(url, PREVIOUS_HEAD)
        async with engine.connect() as conn:
            revision = await conn.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            remaining = (
                await conn.execute(
                    text(
                        "SELECT count(*), count(public_patient_id), "
                        "count(DISTINCT public_patient_id) FROM public.patients"
                    )
                )
            ).one()
            issued_ids_after_downgrade = set(
                (
                    await conn.execute(
                        text("SELECT public_patient_id FROM public.patients")
                    )
                ).scalars()
            )
            unique_constraint_count = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conname = 'uq_patients_public_patient_id'"
                )
            )
        assert revision == CURRENT_HEAD
        assert remaining[0] == remaining[1] == remaining[2]
        assert issued_ids_after_downgrade == issued_ids_before_downgrade
        assert unique_constraint_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_redis_discovery_handle_contracts(monkeypatch) -> None:
    """Exercise the production Lua consume path against a disposable Redis."""
    url = _database_url()
    redis = Redis.from_url(_redis_url(), decode_responses=True)
    await redis.flushdb()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _upgrade(url, CURRENT_HEAD)
        async with factory() as db:
            patient = Patient(is_deleted=False)
            db.add(patient)
            await db.commit()
            service = PatientDiscoveryService(db, redis)
            handle = await service.issue_handle(
                patient=patient,
                provider_id="provider-a",
                hospital_id="hospital-a",
                session_binding="session-a",
                identifier_type="NEXA_PUBLIC_ID",
            )
            assert handle.value not in _handle_key(handle.value)
            stored = await redis.get(_handle_key(handle.value))
            assert stored is not None and handle.value not in stored
            assert json.loads(stored)["state"] == "PENDING_AUDIT"
            with pytest.raises(DiscoveryHandleInvalid):
                await service.consume_handle(
                    raw_handle=handle.value,
                    provider_id="provider-a",
                    hospital_id="hospital-a",
                    session_binding="session-a",
                )
            ttl_before_activation = await redis.pttl(_handle_key(handle.value))
            assert await service.activate_handle(raw_handle=handle.value) is True
            active_payload = json.loads(await redis.get(_handle_key(handle.value)))
            assert active_payload["state"] == "ACTIVE"
            ttl_after_activation = await redis.pttl(_handle_key(handle.value))
            assert 0 < ttl_after_activation <= ttl_before_activation
            assert (
                await redis.ttl(_handle_key(handle.value))
                <= DISCOVERY_HANDLE_TTL_SECONDS
            )
            assert (
                await service.consume_handle(
                    raw_handle=handle.value,
                    provider_id="provider-a",
                    hospital_id="hospital-a",
                    session_binding="session-a",
                )
            ).patient_uuid == patient.patient_uuid
            with pytest.raises(DiscoveryHandleInvalid):
                await service.consume_handle(
                    raw_handle=handle.value,
                    provider_id="provider-a",
                    hospital_id="hospital-a",
                    session_binding="session-a",
                )

            audit_cleanup_handle = await service.issue_handle(
                patient=patient,
                provider_id="provider-a",
                hospital_id="hospital-a",
                session_binding="session-a",
                identifier_type="NEXA_PUBLIC_ID",
            )
            await service.revoke_handle(raw_handle=audit_cleanup_handle.value)
            assert await redis.get(_handle_key(audit_cleanup_handle.value)) is None
            with pytest.raises(DiscoveryHandleInvalid):
                await service.consume_handle(
                    raw_handle=audit_cleanup_handle.value,
                    provider_id="provider-a",
                    hospital_id="hospital-a",
                    session_binding="session-a",
                )

            expiring = await service.issue_handle(
                patient=patient,
                provider_id="provider-a",
                hospital_id="hospital-a",
                session_binding="session-a",
                identifier_type="NEXA_PUBLIC_ID",
            )
            await redis.expire(_handle_key(expiring.value), 1)
            await asyncio.sleep(1.1)
            with pytest.raises(DiscoveryHandleInvalid):
                await service.consume_handle(
                    raw_handle=expiring.value,
                    provider_id="provider-a",
                    hospital_id="hospital-a",
                    session_binding="session-a",
                )

            rate_key = "nexa-qual-1b2:discovery-rate"
            counts = [await atomic_fixed_window(redis, rate_key, 60) for _ in range(13)]
            assert counts[-1][0] == 13
            await redis.delete(rate_key)

            unavailable = Redis.from_url(
                "redis://127.0.0.1:56378/0", socket_connect_timeout=0.2
            )
            try:
                with pytest.raises(Exception):
                    await unavailable.ping()
                with pytest.raises(DiscoveryUnavailable):
                    await PatientDiscoveryService(db, unavailable).issue_handle(
                        patient=patient,
                        provider_id="provider-a",
                        hospital_id="hospital-a",
                        session_binding="session-a",
                        identifier_type="NEXA_PUBLIC_ID",
                    )
            finally:
                await unavailable.close()

            binding_handle = await service.issue_handle(
                patient=patient,
                provider_id="provider-a",
                hospital_id="hospital-a",
                session_binding="session-a",
                identifier_type="NEXA_PUBLIC_ID",
            )
            assert (
                await service.activate_handle(raw_handle=binding_handle.value) is True
            )
            for provider, hospital, session in (
                ("provider-b", "hospital-a", "session-a"),
                ("provider-a", "hospital-b", "session-a"),
                ("provider-a", "hospital-a", "session-b"),
            ):
                with pytest.raises(DiscoveryHandleInvalid):
                    await service.consume_handle(
                        raw_handle=binding_handle.value,
                        provider_id=provider,
                        hospital_id=hospital,
                        session_binding=session,
                    )

            race_handle = await service.issue_handle(
                patient=patient,
                provider_id="provider-a",
                hospital_id="hospital-a",
                session_binding="session-a",
                identifier_type="NEXA_PUBLIC_ID",
            )
            assert await service.activate_handle(raw_handle=race_handle.value) is True

            async def consume_once() -> bool:
                try:
                    await service.consume_handle(
                        raw_handle=race_handle.value,
                        provider_id="provider-a",
                        hospital_id="hospital-a",
                        session_binding="session-a",
                    )
                    return True
                except DiscoveryHandleInvalid:
                    return False

            assert sum(await asyncio.gather(*(consume_once() for _ in range(4)))) == 1

            # A mandatory audit failure must delete the real Redis challenge
            # before returning, while the consumed discovery handle stays spent.
            fake_db = AsyncMock()
            device_result = MagicMock()
            device_result.scalar_one_or_none.return_value = MagicMock()
            no_push_result = MagicMock()
            no_push_result.scalar_one_or_none.return_value = None
            fake_db.execute.side_effect = [device_result, no_push_result]
            cleanup_patient = Patient(is_deleted=False)
            consume_calls = 0

            async def consume_for_cleanup(*_args, **_kwargs):
                nonlocal consume_calls
                consume_calls += 1
                if consume_calls > 1:
                    raise DiscoveryHandleInvalid()
                return cleanup_patient

            monkeypatch.setattr(
                PatientDiscoveryService, "consume_handle", consume_for_cleanup
            )
            monkeypatch.setattr(consent_routes, "get_async_redis_client", lambda: redis)
            monkeypatch.setattr(consent_routes, "get_redis_client", lambda: redis)
            monkeypatch.setattr(
                consent_routes,
                "current_audit_context",
                lambda _domain: MagicMock(),
            )
            monkeypatch.setattr(
                consent_routes,
                "append_audit_log_or_503",
                AsyncMock(side_effect=RuntimeError("audit unavailable")),
            )
            provider = MagicMock(
                actor_uid="provider-a",
                hospital_id="hospital-a",
                session_binding="session-a",
            )
            provider.provider.display_name = "Test clinician"
            provider.hospital.display_name = "Test hospital"
            payload = consent_routes.ConsentChallengeRequestPayload(
                discovery_handle="h" * 32,
                purpose="routine_checkup",
                scope="clinical",
            )
            background_tasks = BackgroundTasks()
            with pytest.raises(HTTPException) as audit_failure:
                await consent_routes.create_consent_request(
                    payload, background_tasks, provider, fake_db
                )
            assert audit_failure.value.status_code == 503
            assert not await redis.keys("consent_request:*")
            assert not background_tasks.tasks
            with pytest.raises(HTTPException) as replay:
                await consent_routes.create_consent_request(
                    payload, BackgroundTasks(), provider, fake_db
                )
            assert replay.value.status_code == 403
    finally:
        await redis.close()
        await engine.dispose()
