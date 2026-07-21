"""Real PostgreSQL contracts for the final runtime correction.

These tests intentionally skip unless TEST_DATABASE_URL identifies a disposable
database. They use real transactions and PostgreSQL locks, never AsyncMock.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_policy import PatientPolicy
from app.services.audit_outbox_processor import _CLAIM_SQL
from app.services.policy_service import PolicyService


pytestmark = pytest.mark.postgres


@pytest.fixture
async def postgres_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_uuid_audit_head_schema_and_foreign_key(postgres_engine):
    async with postgres_engine.connect() as connection:
        types = dict(
            (
                await connection.execute(
                    text(
                        """
            SELECT table_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('audit_ledger', 'audit_id'),
                  ('audit_chain_heads', 'head_event_id')
              )
            """
                    )
                )
            ).all()
        )
        assert types == {"audit_ledger": "uuid", "audit_chain_heads": "uuid"}

        foreign_key = (
            await connection.execute(
                text(
                    """
            SELECT ccu.table_name, ccu.column_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.constraint_schema = kcu.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name AND tc.constraint_schema = ccu.constraint_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
            WHERE tc.constraint_schema = 'public'
              AND tc.table_name = 'audit_chain_heads'
              AND kcu.column_name = 'head_event_id'
            """
                )
            )
        ).one()
        assert tuple(foreign_key) == ("audit_ledger", "audit_id", "RESTRICT")


@pytest.mark.asyncio
async def test_runtime_table_schema_contracts(postgres_engine):
    expected_columns = {
        "patient_policies": {
            "patient_uuid",
            "tenant_id",
            "consent_assurance_policy",
            "updated_at",
            "version",
            "last_idempotency_key",
        },
        "audit_outbox": {
            "id",
            "event_id",
            "idempotency_key",
            "chain_partition",
            "event_type",
            "actor_id",
            "tenant_id",
            "patient_id",
            "payload",
            "status",
            "attempt_count",
            "available_at",
            "processed_at",
            "last_error_code",
            "created_at",
            "processing_started_at",
            "lease_expires_at",
            "worker_id",
        },
        "audit_chain_heads": {
            "chain_partition",
            "head_event_id",
            "head_hash",
            "sequence_number",
            "protocol_version",
            "is_healthy",
            "updated_at",
        },
        "patient_erasure_tombstones": {
            "id",
            "tenant_id",
            "patient_ref",
            "status",
            "assurance_level",
            "wrapping_key_type",
            "patient_wrapping_key_id",
            "kms_state",
            "requested_at",
            "effective_at",
            "scheduled_deletion_date",
            "completion_date",
            "failure_code",
            "operator_action_required",
            "retry_required",
            "audit_event_id",
            "created_at",
            "updated_at",
        },
        "mutation_idempotency": {
            "id",
            "tenant_id",
            "actor_id",
            "operation",
            "resource_id",
            "idempotency_key",
            "request_hash",
            "response_status",
            "response_payload",
            "resulting_resource_version",
            "created_at",
            "retention_expires_at",
        },
    }
    async with postgres_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(:tables)
            """
                ),
                {"tables": list(expected_columns)},
            )
        ).all()
        actual = {table: set() for table in expected_columns}
        for table_name, column_name in rows:
            actual[table_name].add(column_name)
        assert actual == expected_columns

        version = (
            await connection.execute(
                text(
                    """
            SELECT is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'patient_policies'
              AND column_name = 'version'
            """
                )
            )
        ).one()
        assert version[0] == "NO"
        assert version[1] is not None and "1" in version[1]

        hardened_types = {
            (table_name, column_name): (data_type, character_maximum_length)
            for table_name, column_name, data_type, character_maximum_length in (
                await connection.execute(
                    text(
                        """
                        SELECT table_name, column_name, data_type, character_maximum_length
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND (table_name, column_name) IN (
                              ('patient_policies', 'updated_at'),
                              ('audit_ledger', 'chain_scope'),
                              ('audit_outbox', 'chain_partition'),
                              ('audit_chain_heads', 'chain_partition')
                          )
                        """
                    )
                )
            ).all()
        }
        assert hardened_types[("patient_policies", "updated_at")] == (
            "timestamp with time zone",
            None,
        )
        for key in (
            ("audit_ledger", "chain_scope"),
            ("audit_outbox", "chain_partition"),
            ("audit_chain_heads", "chain_partition"),
        ):
            assert hardened_types[key] == ("character varying", 192)

        indexes = set(
            (
                await connection.execute(
                    text(
                        """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'audit_outbox'
            """
                    )
                )
            ).scalars()
        )
        assert {
            "uq_audit_outbox_tenant_idempotency",
            "uq_audit_outbox_global_idempotency",
            "ix_audit_outbox_status_available_at",
            "ix_audit_outbox_expired_lease",
            "ix_audit_outbox_dead_letter",
        } <= indexes


@pytest.mark.asyncio
async def test_policy_orm_round_trip_version_and_constraints(postgres_engine):
    patient_id = uuid.uuid4()
    async with postgres_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:patient_id)"),
                {"patient_id": patient_id},
            )
            async with AsyncSession(
                bind=connection, expire_on_commit=False
            ) as first_session:
                policy = PatientPolicy(
                    patient_uuid=patient_id,
                    tenant_id="migration-test-tenant",
                    consent_assurance_policy="standard",
                    version=1,
                    last_idempotency_key="initial-write",
                )
                first_session.add(policy)
                await first_session.flush()
                await first_session.refresh(policy)
                assert policy.patient_uuid == patient_id
                assert policy.version == 1

                updated = await first_session.execute(
                    text(
                        """
                    UPDATE public.patient_policies
                    SET version = version + 1,
                        last_idempotency_key = 'versioned-write'
                    WHERE patient_uuid = :patient_id AND version = 1
                    RETURNING version
                    """
                    ),
                    {"patient_id": patient_id},
                )
                assert updated.scalar_one() == 2

            # Use a distinct identity map to model a second database client and
            # avoid masking the database constraint with an ORM identity warning.
            async with AsyncSession(
                bind=connection, expire_on_commit=False
            ) as second_session:
                with pytest.raises(IntegrityError):
                    async with second_session.begin_nested():
                        second_session.add(
                            PatientPolicy(
                                patient_uuid=patient_id,
                                tenant_id="migration-test-tenant",
                                consent_assurance_policy="standard",
                                version=1,
                            )
                        )
                        await second_session.flush()

            async with AsyncSession(
                bind=connection, expire_on_commit=False
            ) as constraint_session:
                with pytest.raises(IntegrityError):
                    async with constraint_session.begin_nested():
                        await constraint_session.execute(
                            text(
                                """
                            INSERT INTO public.patient_policies
                                (patient_uuid, tenant_id, consent_assurance_policy, version)
                            VALUES (gen_random_uuid(), 'migration-test-tenant', 'standard', NULL)
                            """
                            )
                        )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_policy_service_atomic_real_postgres_contract(postgres_engine):
    patient_id = uuid.uuid4()
    tenant_id = "t" * 128
    actor_id = f"provider-{uuid.uuid4()}"
    first_key = f"policy-create-{uuid.uuid4().hex}"
    update_key = f"policy-update-{uuid.uuid4().hex}"

    async with postgres_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:patient_id)"),
                {"patient_id": patient_id},
            )
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                service = PolicyService(session)
                created = await service.set_policy_atomic(
                    patient_id,
                    "biometric_required",
                    expected_version=0,
                    idempotency_key=first_key,
                    actor_id=actor_id,
                    tenant_id=tenant_id,
                )
                assert created.version == 1
                assert created.idempotent_replay is False

                updated = await service.set_policy_atomic(
                    patient_id,
                    "break_glass_restricted",
                    expected_version=1,
                    idempotency_key=update_key,
                    actor_id=actor_id,
                    tenant_id=tenant_id,
                )
                assert updated.version == 2
                assert updated.idempotent_replay is False

                replayed = await service.set_policy_atomic(
                    patient_id,
                    "break_glass_restricted",
                    expected_version=1,
                    idempotency_key=update_key,
                    actor_id=actor_id,
                    tenant_id=tenant_id,
                )
                assert replayed.version == 2
                assert replayed.idempotent_replay is True

                persisted = (
                    await session.execute(
                        text(
                            """
                            SELECT consent_assurance_policy, version, updated_at
                            FROM public.patient_policies
                            WHERE patient_uuid = :patient_id
                            """
                        ),
                        {"patient_id": patient_id},
                    )
                ).one()
                assert persisted.consent_assurance_policy == "break_glass_restricted"
                assert persisted.version == 2
                assert isinstance(persisted.updated_at, datetime)
                assert persisted.updated_at.tzinfo is not None

                outbox_count = (
                    await session.execute(
                        text(
                            """
                            SELECT count(*) FROM public.audit_outbox
                            WHERE tenant_id = :tenant_id AND patient_id = :patient_id
                            """
                        ),
                        {"tenant_id": tenant_id, "patient_id": str(patient_id)},
                    )
                ).scalar_one()
                assert outbox_count == 2
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_mutation_idempotency_scope_constraint(postgres_engine):
    async with postgres_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            values = {
                "tenant": "migration-test-tenant",
                "key": f"migration-test-{uuid.uuid4().hex}",
            }
            insert = text(
                """
                INSERT INTO public.mutation_idempotency
                    (tenant_id, actor_id, operation, resource_id, idempotency_key, request_hash)
                VALUES (:tenant, 'test-actor', 'policy_update', 'test-resource', :key, :hash)
                """
            )
            await connection.execute(insert, {**values, "hash": "a" * 64})
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(insert, {**values, "hash": "b" * 64})
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_outbox_active_lease_is_not_double_claimed(postgres_engine):
    outbox_id = uuid.uuid4()
    params = {
        "id": outbox_id,
        "idempotency_key": f"postgres-lease-{uuid.uuid4().hex}",
        "partition": f"tenant:{uuid.uuid4()}:policy",
    }
    async with postgres_engine.begin() as connection:
        await connection.execute(
            text(
                """
            INSERT INTO public.audit_outbox
                (id, event_id, idempotency_key, chain_partition, event_type, actor_id,
                 tenant_id, patient_id, payload, status, attempt_count, available_at, created_at)
            VALUES
                (:id, gen_random_uuid(), :idempotency_key, :partition, 'POSTGRES_LEASE_TEST',
                 'test-worker', 'test-tenant', NULL, '{}'::jsonb, 'pending', 0, now(), now())
            """
            ),
            params,
        )
    try:
        async with postgres_engine.begin() as first:
            first_rows = (
                (
                    await first.execute(
                        _CLAIM_SQL,
                        {
                            "batch_size": 1,
                            "lease_seconds": 60,
                            "worker_id": "worker-one",
                        },
                    )
                )
                .mappings()
                .all()
            )
        async with postgres_engine.begin() as second:
            second_rows = (
                (
                    await second.execute(
                        _CLAIM_SQL,
                        {
                            "batch_size": 1,
                            "lease_seconds": 60,
                            "worker_id": "worker-two",
                        },
                    )
                )
                .mappings()
                .all()
            )
        assert [row["id"] for row in first_rows] == [outbox_id]
        assert all(row["id"] != outbox_id for row in second_rows)
    finally:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.audit_outbox WHERE id = :id"),
                {"id": outbox_id},
            )


@pytest.mark.asyncio
async def test_expired_outbox_lease_is_reclaimed(postgres_engine):
    outbox_id = uuid.uuid4()
    async with postgres_engine.begin() as connection:
        await connection.execute(
            text(
                """
            INSERT INTO public.audit_outbox
                (id, event_id, idempotency_key, chain_partition, event_type, actor_id,
                 tenant_id, payload, status, attempt_count, available_at, created_at,
                 processing_started_at, lease_expires_at, worker_id)
            VALUES
                (:id, gen_random_uuid(), :key, 'tenant:test:policy', 'POSTGRES_RECLAIM_TEST',
                 'test-worker', 'test-tenant', '{}'::jsonb, 'processing', 0, now(), now(),
                 now() - interval '2 minutes', now() - interval '1 minute', 'crashed-worker')
            """
            ),
            {"id": outbox_id, "key": f"postgres-reclaim-{uuid.uuid4().hex}"},
        )
    try:
        async with postgres_engine.begin() as connection:
            rows = (
                (
                    await connection.execute(
                        _CLAIM_SQL,
                        {
                            "batch_size": 1,
                            "lease_seconds": 60,
                            "worker_id": "replacement-worker",
                        },
                    )
                )
                .mappings()
                .all()
            )
        assert [row["id"] for row in rows] == [outbox_id]
    finally:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.audit_outbox WHERE id = :id"),
                {"id": outbox_id},
            )
