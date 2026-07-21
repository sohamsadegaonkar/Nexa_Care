from __future__ import annotations

import inspect
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_policy import PatientPolicy

_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")

_CAS_UPDATE_SQL = text(
    """
    UPDATE patient_policies
    SET
        consent_assurance_policy = :new_policy,
        version = version + 1,
        updated_at = :now,
        last_idempotency_key = :idempotency_key
    WHERE
        patient_uuid = :patient_uuid
        AND tenant_id = :tenant_id
        AND version = :expected_version
    RETURNING version, consent_assurance_policy
    """
)

_FIRST_WRITE_INSERT_SQL = text(
    """
    INSERT INTO patient_policies
        (patient_uuid, tenant_id, consent_assurance_policy, updated_at, version, last_idempotency_key)
    VALUES
        (:patient_uuid, :tenant_id, :new_policy, :now, 1, :idempotency_key)
    ON CONFLICT (tenant_id, patient_uuid) DO NOTHING
    RETURNING version, consent_assurance_policy
    """
)

_IDEMPOTENCY_SELECT_SQL = text(
    """
    SELECT request_hash, response_status, response_payload, resulting_resource_version
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :idempotency_key
    """
)

_IDEMPOTENCY_RESERVE_SQL = text(
    """
    INSERT INTO public.mutation_idempotency
        (tenant_id, actor_id, operation, resource_id, idempotency_key, request_hash,
         created_at, retention_expires_at)
    VALUES
        (:tenant_id, :actor_id, :operation, :resource_id, :idempotency_key, :request_hash,
         now(), now() + interval '90 days')
    ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
    RETURNING id
    """
)

_IDEMPOTENCY_COMPLETE_SQL = text(
    """
    UPDATE public.mutation_idempotency
    SET response_status = 200,
        response_payload = CAST(:response_payload AS JSONB),
        resulting_resource_version = :version
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :idempotency_key
    """
)

_OUTBOX_INSERT_SQL = text(
    """
    INSERT INTO public.audit_outbox
        (id, event_id, idempotency_key, chain_partition, event_type, actor_id,
         tenant_id, patient_id, payload, status, attempt_count, available_at, created_at)
    VALUES
        (gen_random_uuid(), gen_random_uuid(), :idempotency_key, :chain_partition, :event_type, :actor_id,
         :tenant_id, :patient_id, CAST(:payload AS JSONB), 'pending', 0, now(), now())
    """
)


class PolicyValidationError(ValueError):
    """Raised for a malformed idempotency key or missing required field."""


class PolicyVersionConflict(RuntimeError):
    """Raised when the compare-and-swap does not match the current version."""


class PolicyIdempotencyKeyReused(RuntimeError):
    """Raised when the same idempotency key is reused with a different payload."""


@dataclass(frozen=True, slots=True)
class PolicyUpdateResult:
    patient_uuid: str
    consent_assurance_policy: str
    version: int
    idempotent_replay: bool


def validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise PolicyValidationError(
            "idempotency_key must be 8-128 characters of letters, digits, '_', '.', ':' or '-'."
        )
    return value


def _outbox_payload(*, patient_uuid: str, old_policy: str | None, new_policy: str, actor_id: str, version: int) -> str:
    return json.dumps(
        {
            "patient_uuid": patient_uuid,
            "old_policy": old_policy,
            "new_policy": new_policy,
            "actor_id": actor_id,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_request_hash(
    *, tenant_id: str, patient_uuid: UUID, actor_id: str, operation: str,
    new_policy: str, expected_version: int,
) -> str:
    canonical = json.dumps(
        {
            "actor_id": actor_id,
            "expected_version": expected_version,
            "operation": operation,
            "patient_uuid": str(patient_uuid),
            "requested_policy": new_policy,
            "tenant_id": tenant_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_result(row, patient_uuid: UUID, request_hash: str) -> PolicyUpdateResult:
    if row.request_hash != request_hash:
        raise PolicyIdempotencyKeyReused("The idempotency key was already used with a different request.")
    payload = row.response_payload
    if not isinstance(payload, dict) or row.response_status != 200:
        raise PolicyValidationError("The prior idempotent mutation has no completed safe response.")
    return PolicyUpdateResult(
        patient_uuid=str(patient_uuid),
        consent_assurance_policy=str(payload["consent_assurance_policy"]),
        version=int(row.resulting_resource_version),
        idempotent_replay=True,
    )


class PolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_policy(self, patient_uuid: UUID) -> str:
        policy = await self.db.get(PatientPolicy, patient_uuid)
        if policy is not None:
            value = getattr(policy, "consent_assurance_policy", None)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, str) and value.strip():
                return value
        return "standard"

    async def get_policy_row(self, patient_uuid: UUID) -> PatientPolicy | None:
        return await self.db.get(PatientPolicy, patient_uuid)

    async def set_policy(self, patient_uuid: UUID, policy: str) -> str:
        """Legacy non-CAS upsert. Retained only for callers (dev simulator,
        internal tooling) that intentionally bypass optimistic concurrency
        and idempotency. Real clinician-facing policy mutation must go
        through set_policy_atomic()."""
        now = datetime.now(timezone.utc).isoformat()
        stmt = (
            insert(PatientPolicy)
            .values(patient_uuid=patient_uuid, consent_assurance_policy=policy, updated_at=now, version=1)
            .on_conflict_do_update(
                index_elements=[PatientPolicy.patient_uuid],
                set_={"consent_assurance_policy": policy, "updated_at": now,
                      "version": PatientPolicy.version + 1},
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()
        return policy

    async def set_policy_atomic(
        self,
        patient_uuid: UUID,
        new_policy: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor_id: str,
        tenant_id: str,
        event_type: str = "PATIENT_POLICY_CHANGED",
    ) -> PolicyUpdateResult:
        """Atomically: CAS-update the policy -> insert the audit-outbox
        event -> commit. Exactly one transaction, one commit, no
        intermediate commit before the outbox write (Defect 6)."""

        validate_idempotency_key(idempotency_key)
        if not tenant_id.strip():
            raise PolicyValidationError("A trusted tenant context is required for policy audit events.")
        chain_partition = f"tenant:{tenant_id}:policy"

        operation = "patient_policy_update"
        request_hash = _canonical_request_hash(
            tenant_id=tenant_id, patient_uuid=patient_uuid, actor_id=actor_id,
            operation=operation, new_policy=new_policy, expected_version=expected_version,
        )
        existing_idempotency = (
            await self.db.execute(
                _IDEMPOTENCY_SELECT_SQL,
                {"tenant_id": tenant_id, "operation": operation, "idempotency_key": idempotency_key},
            )
        ).first()
        if existing_idempotency is not None:
            return _replay_result(existing_idempotency, patient_uuid, request_hash)

        reservation = await self.db.execute(
            _IDEMPOTENCY_RESERVE_SQL,
            {
                "tenant_id": tenant_id, "actor_id": actor_id, "operation": operation,
                "resource_id": str(patient_uuid), "idempotency_key": idempotency_key,
                "request_hash": request_hash,
            },
        )
        if reservation.first() is None:
            concurrent = (
                await self.db.execute(
                    _IDEMPOTENCY_SELECT_SQL,
                    {"tenant_id": tenant_id, "operation": operation, "idempotency_key": idempotency_key},
                )
            ).first()
            if concurrent is None:
                raise PolicyValidationError("Could not resolve the concurrent idempotent mutation.")
            return _replay_result(concurrent, patient_uuid, request_hash)

        existing = await self.get_policy_row(patient_uuid)

        # Captured now, before any UPDATE executes -- never read back off
        # `existing` after the mutation, since that risks returning the
        # already-updated value depending on session/ORM object lifecycle.
        old_policy = existing.consent_assurance_policy if existing is not None else None

        now = datetime.now(timezone.utc)

        if existing is None:
            if expected_version != 0:
                raise PolicyVersionConflict("Patient has no existing policy; expected_version must be 0.")
            result = await self.db.execute(
                _FIRST_WRITE_INSERT_SQL,
                {
                    "patient_uuid": patient_uuid,
                    "new_policy": new_policy,
                    "now": now,
                    "idempotency_key": idempotency_key,
                    "tenant_id": tenant_id,
                },
            )
            row = result.first()
            if row is None:
                # Someone else created the row concurrently between our read and write.
                await self.db.rollback()
                raise PolicyVersionConflict("Policy was created concurrently by another request.")
            new_version, stored_policy = row
        else:
            result = await self.db.execute(
                _CAS_UPDATE_SQL,
                {
                    "new_policy": new_policy,
                    "now": now,
                    "idempotency_key": idempotency_key,
                    "patient_uuid": patient_uuid,
                    "expected_version": expected_version,
                    "tenant_id": tenant_id,
                },
            )
            row = result.first()
            if row is None:
                await self.db.rollback()
                raise PolicyVersionConflict(
                    f"Expected version {expected_version} did not match the current policy version."
                )
            new_version, stored_policy = row

        await self.db.execute(
            _OUTBOX_INSERT_SQL,
            {
                "idempotency_key": idempotency_key,
                "chain_partition": chain_partition,
                "event_type": event_type,
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "patient_id": str(patient_uuid),
                "payload": _outbox_payload(
                    patient_uuid=str(patient_uuid),
                    old_policy=old_policy,
                    new_policy=stored_policy,
                    actor_id=actor_id,
                    version=new_version,
                ),
            },
        )

        safe_response = {
            "patient_uuid": str(patient_uuid),
            "consent_assurance_policy": stored_policy,
            "version": new_version,
        }
        await self.db.execute(
            _IDEMPOTENCY_COMPLETE_SQL,
            {
                "tenant_id": tenant_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
                "response_payload": json.dumps(safe_response, sort_keys=True, separators=(",", ":")),
                "version": new_version,
            },
        )

        await self.db.commit()

        return PolicyUpdateResult(
            patient_uuid=str(patient_uuid),
            consent_assurance_policy=stored_policy,
            version=new_version,
            idempotent_replay=False,
        )
