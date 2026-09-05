"""Real PostgreSQL proof for vault-width and transactional DEK invariants."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.dek_store import PatientDEKStore
from app.models.shards import NexaVault
from app.services.crypto_kms import LocalEnvelopeProvider, PatientDataErased
from app.services.sharding import decrypt_vault_field, encrypt_vault_payload
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    normalize_async_postgres_url,
    normalize_sync_postgres_url,
    postgres_database_url,
    require_disposable_database_name,
    require_loopback_postgres_url,
)

pytestmark = pytest.mark.postgres

PREVIOUS_HEAD = "20260818_async_provider_jobs"
CURRENT_HEAD = "20260819_widen_vault_pii_columns"


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    if not value:
        value = postgres_database_url("nexa_qual_ci_shared")
    normalized = normalize_async_postgres_url(value)
    require_loopback_postgres_url(normalized)
    require_disposable_database_name(urlsplit(normalized).path.lstrip("/"))
    return normalized


@pytest.fixture
def env_setup():
    from unittest.mock import patch

    with patch.dict(
        os.environ,
        {
            "KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!",
            "ENCRYPTION_BACKEND": "local",
        },
    ):
        yield


async def _cleanup(factory) -> None:
    async with factory() as db:
        await db.execute(delete(NexaVault))
        await db.execute(delete(PatientDEKStore))
        await db.commit()


@pytest.mark.asyncio
async def test_postgres_vault_width_migration_lifecycle(env_setup, monkeypatch):
    """Prove previous-head preservation, widening, and downgrade safety."""
    db_name = "nexa_qual_vault_width_mig"
    url = await create_disposable_database(db_name)
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", normalize_sync_postgres_url(url))

    # This qualification requires a fresh disposable database. Setup failures
    engine = None
    try:
        await asyncio.to_thread(command.upgrade, alembic_cfg, PREVIOUS_HEAD)
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        kms = LocalEnvelopeProvider()
        legacy_patient_id = str(uuid.uuid4())
        legacy_plaintext = "Nexa"
        async with factory() as db:
            await kms.generate_dek(legacy_patient_id, db)
            legacy_ciphertext = (
                await encrypt_vault_payload(
                    {"patient_name": legacy_plaintext}, legacy_patient_id, db, kms
                )
            )["patient_name"]
            assert legacy_ciphertext is not None
            assert len(legacy_ciphertext) <= 255
            db.add(
                NexaVault(
                    masked_internal_id=legacy_patient_id,
                    patient_name=legacy_ciphertext,
                )
            )
            await db.commit()

        # A real serialized encrypted phone value cannot fit the old schema.
        phone_patient_id = str(uuid.uuid4())
        async with factory() as db:
            await kms.generate_dek(phone_patient_id, db)
            phone_ciphertext = (
                await encrypt_vault_payload(
                    {"phone": "+919876543210"}, phone_patient_id, db, kms
                )
            )["phone"]
            assert phone_ciphertext is not None
            assert len(phone_ciphertext) > 32
            with pytest.raises(Exception) as exc_info:
                await db.execute(
                    text(
                        """
                        INSERT INTO public.nexa_vault
                            (id, masked_internal_id, phone, created_at, updated_at)
                        VALUES
                            (gen_random_uuid(), :masked_id, :phone, now(), now())
                        """
                    ),
                    {"masked_id": phone_patient_id, "phone": phone_ciphertext},
                )
                await db.commit()
            assert "value too long" in str(exc_info.value).lower()
            await db.rollback()

        await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

        async with factory() as db:
            preserved = await db.scalar(
                select(NexaVault).where(
                    NexaVault.masked_internal_id == legacy_patient_id
                )
            )
            assert preserved is not None
            assert preserved.patient_name == legacy_ciphertext
            assert (
                await decrypt_vault_field(
                    legacy_patient_id,
                    "patient_name",
                    preserved.patient_name,
                    db,
                    kms,
                )
                == legacy_plaintext
            )

        current_patient_id = str(uuid.uuid4())
        payload = {
            "patient_name": "Synthetic qualification name",
            "phone": "+919876543210",
            "aadhaar_abha_id": "synthetic.abha@abdm",
        }
        async with factory() as db:
            await kms.generate_dek(current_patient_id, db)
            encrypted = await encrypt_vault_payload(
                payload, current_patient_id, db, kms
            )
            db.add(NexaVault(masked_internal_id=current_patient_id, **encrypted))
            await db.commit()
            persisted = await db.scalar(
                select(NexaVault).where(
                    NexaVault.masked_internal_id == current_patient_id
                )
            )
            assert persisted is not None
            assert persisted.phone == encrypted["phone"]
            assert persisted.aadhaar_abha_id == encrypted["aadhaar_abha_id"]
            assert (
                await decrypt_vault_field(
                    current_patient_id, "phone", persisted.phone, db, kms
                )
                == payload["phone"]
            )

        with pytest.raises(Exception, match="VAULT_WIDEN_DOWNGRADE_BLOCKED"):
            await asyncio.to_thread(command.downgrade, alembic_cfg, PREVIOUS_HEAD)

        await _cleanup(factory)
        await asyncio.to_thread(command.downgrade, alembic_cfg, PREVIOUS_HEAD)
        await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)
    finally:
        if engine is not None:
            await engine.dispose()
        await drop_disposable_database(db_name)


@pytest.mark.asyncio
async def test_postgres_transactional_dek_lifecycle(env_setup, monkeypatch):
    """Prove rollback, cache binding, serialization, rotation, and erasure."""
    url = _url()
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, CURRENT_HEAD)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider_a = LocalEnvelopeProvider()
    provider_b = LocalEnvelopeProvider()
    try:
        patient_id = str(uuid.uuid4())
        patient_uuid = uuid.UUID(patient_id)

        class _SyntheticRollback(Exception):
            pass

        # A caches a staged v1, but its transaction rolls back.
        with pytest.raises(_SyntheticRollback):
            async with factory() as db:
                async with db.begin():
                    staged = await provider_a.ensure_active_dek(patient_id, db)
                    assert staged.dek_version == 1
                    db.add(
                        NexaVault(
                            masked_internal_id=patient_id,
                            phone=(
                                await provider_a.encrypt_field(
                                    patient_id, "phone", "+919876543210", db
                                )
                            ).serialize(),
                        )
                    )
                    await db.flush()
                    raise _SyntheticRollback()

        async with factory() as db:
            assert (
                await db.scalar(
                    select(PatientDEKStore).where(
                        PatientDEKStore.patient_id == patient_uuid
                    )
                )
                is None
            )

        # B commits a distinct v1. A must reject its cache entry once the
        # durable row fingerprint changes, then encrypt/decrypt with B's key.
        async with factory() as db:
            async with db.begin():
                committed = await provider_b.ensure_active_dek(patient_id, db)
                assert committed.dek_version == 1

        async with factory() as db:
            row = await db.scalar(
                select(PatientDEKStore).where(
                    PatientDEKStore.patient_id == patient_uuid
                )
            )
            assert row is not None
            encrypted = await provider_a.encrypt_field(
                patient_id, "phone", "+919876543210", db
            )
            assert (
                await provider_a.decrypt_field(patient_id, "phone", encrypted, db)
                == "+919876543210"
            )
            assert (
                await provider_b.decrypt_field(patient_id, "phone", encrypted, db)
                == "+919876543210"
            )
            assert (
                provider_a._get_cached_dek(
                    patient_id, 1, provider_a._cache_identity(row)
                )
                is not None
            )
            assert (
                len(
                    list(
                        (
                            await db.scalars(
                                select(PatientDEKStore).where(
                                    PatientDEKStore.patient_id == patient_uuid
                                )
                            )
                        ).all()
                    )
                )
                == 1
            )

        # Five first-use transactions serialize to exactly one active v1 row.
        first_patient_id = str(uuid.uuid4())
        first_uuid = uuid.UUID(first_patient_id)

        async def first_ensure_worker() -> int:
            async with factory() as db:
                async with db.begin():
                    return (
                        await LocalEnvelopeProvider().ensure_active_dek(
                            first_patient_id, db
                        )
                    ).dek_version

        assert await asyncio.gather(*[first_ensure_worker() for _ in range(5)]) == [
            1,
            1,
            1,
            1,
            1,
        ]
        async with factory() as db:
            assert (
                len(
                    list(
                        (
                            await db.scalars(
                                select(PatientDEKStore).where(
                                    PatientDEKStore.patient_id == first_uuid
                                )
                            )
                        ).all()
                    )
                )
                == 1
            )

        # ensure and rotate share the same transaction-scoped lock. Rotation
        # waits until ensure releases its lock, then creates the next version.
        race_patient_id = str(uuid.uuid4())
        async with factory() as db:
            await provider_a.generate_dek(race_patient_id, db)
        # Legacy generate_dek is an explicit v1 create, not a get-or-create
        # alias for ensure_active_dek. The existing unique constraint remains
        # the authoritative duplicate protection.
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await provider_b.generate_dek(race_patient_id, db)
            await db.rollback()
        ensure_entered = asyncio.Event()

        async def ensure_worker() -> int:
            async with factory() as db:
                async with db.begin():
                    bundle = await provider_a.ensure_active_dek(race_patient_id, db)
                    ensure_entered.set()
                    await asyncio.sleep(0.1)
                    return bundle.dek_version

        async def rotate_worker() -> int:
            await ensure_entered.wait()
            async with factory() as db:
                return (await provider_b.rotate_dek(race_patient_id, db)).dek_version

        assert await asyncio.gather(ensure_worker(), rotate_worker()) == [1, 2]

        async def rotate_again() -> int:
            async with factory() as db:
                return (
                    await LocalEnvelopeProvider().rotate_dek(race_patient_id, db)
                ).dek_version

        assert set(await asyncio.gather(rotate_again(), rotate_again())) == {3, 4}
        async with factory() as db:
            active_rows = list(
                (
                    await db.scalars(
                        select(PatientDEKStore).where(
                            PatientDEKStore.patient_id == uuid.UUID(race_patient_id),
                            PatientDEKStore.is_active,
                        )
                    )
                ).all()
            )
            assert len(active_rows) == 1
            assert active_rows[0].dek_version == 4

        # A durable destroyed marker blocks lifecycle operations even when this
        # focused PostgreSQL probe does not create an erasure tombstone.
        erased_patient_id = str(uuid.uuid4())
        async with factory() as db:
            await provider_a.generate_dek(erased_patient_id, db)
            await db.execute(
                update(PatientDEKStore)
                .where(PatientDEKStore.patient_id == uuid.UUID(erased_patient_id))
                .values(is_active=False, destroyed_at=datetime.now(timezone.utc))
            )
            await db.commit()

        for operation in ("generate_dek", "ensure_active_dek", "rotate_dek"):
            async with factory() as db:
                with pytest.raises(PatientDataErased):
                    await getattr(provider_a, operation)(erased_patient_id, db)

        await _cleanup(factory)
    finally:
        await engine.dispose()
