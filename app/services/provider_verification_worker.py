"""Durable internal worker for server-owned provider-registry verification."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    ProviderVerificationWork,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
    VerificationWorkStatus,
)
from app.services.provider_verification_application import (
    ProviderVerificationApplicationService,
    RegistryLookupInvocation,
    SourceAutomationPolicyRegistry,
    ValidatedRegistryLookupEnvelope,
    execute_lookup_and_create_envelope,
)
from app.services.provider_verification_registry import (
    FacilityLookupRequest,
    ProfessionalLookupRequest,
    RegistryAdapter,
    RegistryAdapterContractError,
    RegistryAdapterError,
    RegistryObservation,
    RegistryObservationInvalidError,
    RegistryRequestInvalidError,
    RegistryResourceType,
    RegistryTransientUnavailableError,
    RegistryUnsupportedResourceError,
)

_DEFAULT_LEASE_SECONDS = 60
_BASE_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 3600


def _compute_backoff(attempt_count: int) -> int:
    return min(
        _BASE_BACKOFF_SECONDS * 2 ** max(0, attempt_count - 1),
        _MAX_BACKOFF_SECONDS,
    )


class ProviderVerificationWorkerError(RuntimeError):
    """Stable internal worker failure without registry payload disclosure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderVerificationWorkerService:
    """Claims work and applies one stable logical invocation per work row.

    Network I/O is outside database transactions. Its durable attempt counter
    commits before the call. The resulting observation, Phase-5E savepoint, and
    work terminalization share one outer transaction, preventing split commits.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        worker_id: str | None = None,
        adapters: dict[str, RegistryAdapter] | None = None,
        source_policies: SourceAutomationPolicyRegistry | None = None,
        automation_enabled: bool | Any = False,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.db = db
        self.worker_id = worker_id or f"worker:{uuid.uuid4()}"
        self.adapters = adapters or {}
        self.source_policies = source_policies or SourceAutomationPolicyRegistry()
        self._automation_enabled = automation_enabled
        self.lease_seconds = lease_seconds

    def is_automation_enabled(self) -> bool:
        return bool(
            self._automation_enabled()
            if callable(self._automation_enabled)
            else self._automation_enabled
        )

    def _transaction(self) -> Any:
        return self.db.begin_nested() if self.db.in_transaction() else self.db.begin()

    async def claim_work_batch(
        self, *, batch_size: int = 10, now: datetime | None = None
    ) -> list[ProviderVerificationWork]:
        if not self.is_automation_enabled():
            return []
        moment = _aware(now)
        async with self._transaction():
            rows = (
                (
                    await self.db.execute(
                        select(ProviderVerificationWork)
                        .where(
                            or_(
                                ProviderVerificationWork.status
                                == VerificationWorkStatus.PENDING.value,
                                (
                                    ProviderVerificationWork.status
                                    == VerificationWorkStatus.CLAIMED.value
                                )
                                & (
                                    ProviderVerificationWork.lease_expires_at
                                    <= func.now()
                                ),
                            ),
                            ProviderVerificationWork.next_attempt_at <= moment,
                        )
                        .order_by(
                            ProviderVerificationWork.priority.desc(),
                            ProviderVerificationWork.next_attempt_at.asc(),
                        )
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for work in rows:
                work.status = VerificationWorkStatus.CLAIMED.value
                work.lease_owner = self.worker_id
                work.lease_expires_at = moment + timedelta(seconds=self.lease_seconds)
            if rows:
                await self.db.flush()
                await self._after_claim_locked(rows)
            return rows

    async def _after_claim_locked(self, rows: list[ProviderVerificationWork]) -> None:
        """Extension seam executed while claimed rows remain transaction-locked.

        Normal production execution intentionally does nothing.  PostgreSQL
        qualification uses a subclass to hold one real lock long enough to
        prove the second worker's ``SKIP LOCKED`` behavior.
        """
        return None

    async def process_work_item(
        self, work: ProviderVerificationWork, *, now: datetime | None = None
    ) -> VerificationWorkStatus:
        """Process a claimed row using its durable id and creation timestamp."""
        moment = _aware(now)
        resource_type = self._resource_type(work)
        if not self.is_automation_enabled():
            return await self._terminalize_without_application(
                work.id,
                VerificationWorkStatus.CANCELLED_POLICY,
                moment,
                "AUTOMATION_DISABLED",
            )
        policy = self.source_policies.get_policy(
            work.source_id,
            resource_type=resource_type,
            registration_authority_code=work.registration_authority_code,
        )
        if not policy.automation_enabled:
            return await self._terminalize_without_application(
                work.id,
                VerificationWorkStatus.CANCELLED_POLICY,
                moment,
                "SOURCE_POLICY_DISABLED",
            )
        adapter = self.adapters.get(work.source_id)
        if adapter is None:
            return await self._terminalize_without_application(
                work.id,
                VerificationWorkStatus.FAILED_TERMINAL,
                moment,
                "ADAPTER_UNAVAILABLE",
            )

        attempted = await self._record_outbound_attempt(work.id, moment)
        if attempted is None:
            return VerificationWorkStatus.CLAIMED
        work = attempted
        invocation = RegistryLookupInvocation(
            resource_id=self._target_id(work),
            resource_type=resource_type,
            expected_version=work.expected_resource_version,
            request=self._request(work, resource_type),
            invoked_at=self._invocation_timestamp(work),
            invocation_id=work.id,
        )
        try:
            envelope = await execute_lookup_and_create_envelope(adapter, invocation)
        except RegistryTransientUnavailableError:
            return await self._retry_or_exhaust(
                work, resource_type, invocation, adapter, moment
            )
        except (
            RegistryAdapterContractError,
            RegistryUnsupportedResourceError,
            RegistryRequestInvalidError,
            RegistryObservationInvalidError,
        ) as exc:
            return await self._terminalize_without_application(
                work.id,
                VerificationWorkStatus.FAILED_TERMINAL,
                moment,
                getattr(exc, "error_code", "REGISTRY_CONTRACT_ERROR"),
            )
        except RegistryAdapterError as exc:
            return await self._terminalize_without_application(
                work.id,
                VerificationWorkStatus.FAILED_TERMINAL,
                moment,
                getattr(exc, "error_code", "REGISTRY_ADAPTER_ERROR"),
            )
        except Exception:
            return await self._terminalize_without_application(
                work.id,
                VerificationWorkStatus.FAILED_TERMINAL,
                moment,
                "WORKER_INTERNAL_ERROR",
            )
        return await self._apply_and_terminalize(
            work.id,
            envelope,
            invocation,
            VerificationWorkStatus.COMPLETED,
            moment,
            None,
        )

    @staticmethod
    def _resource_type(work: ProviderVerificationWork) -> RegistryResourceType:
        if work.professional_verification_id is not None:
            return RegistryResourceType.PROFESSIONAL
        if work.facility_verification_id is not None:
            return RegistryResourceType.FACILITY
        raise ProviderVerificationWorkerError("INVALID_WORK_ITEM")

    @staticmethod
    def _target_id(work: ProviderVerificationWork) -> UUID:
        target_id = work.professional_verification_id or work.facility_verification_id
        if target_id is None:
            raise ProviderVerificationWorkerError("INVALID_WORK_ITEM")
        return target_id

    @staticmethod
    def _invocation_timestamp(work: ProviderVerificationWork) -> datetime:
        if not isinstance(work.created_at, datetime) or work.created_at.tzinfo is None:
            raise ProviderVerificationWorkerError("WORK_INVOCATION_TIMESTAMP_INVALID")
        return work.created_at.astimezone(timezone.utc)

    @staticmethod
    def _request(
        work: ProviderVerificationWork, resource_type: RegistryResourceType
    ) -> ProfessionalLookupRequest | FacilityLookupRequest:
        common = {
            "registration_authority_code": work.registration_authority_code,
            "registration_number_normalized": work.registration_number_normalized,
            "lookup_purpose": VerificationEvidenceLookupPurpose(work.lookup_purpose),
        }
        if resource_type == RegistryResourceType.PROFESSIONAL:
            return ProfessionalLookupRequest(**common)
        return FacilityLookupRequest(**common)

    async def _lock_claim(self, work_id: UUID) -> ProviderVerificationWork | None:
        return (
            await self.db.execute(
                select(ProviderVerificationWork)
                .where(
                    ProviderVerificationWork.id == work_id,
                    ProviderVerificationWork.status
                    == VerificationWorkStatus.CLAIMED.value,
                    ProviderVerificationWork.lease_owner == self.worker_id,
                    ProviderVerificationWork.lease_expires_at > func.now(),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _record_outbound_attempt(
        self, work_id: UUID, moment: datetime
    ) -> ProviderVerificationWork | None:
        async with self._transaction():
            work = await self._lock_claim(work_id)
            if work is None:
                return None
            work.attempt_count += 1
            work.last_attempted_at = moment
            await self.db.flush()
            return work

    async def _retry_or_exhaust(
        self,
        work: ProviderVerificationWork,
        resource_type: RegistryResourceType,
        invocation: RegistryLookupInvocation,
        adapter: RegistryAdapter,
        moment: datetime,
    ) -> VerificationWorkStatus:
        if work.attempt_count < work.max_attempts:
            async with self._transaction():
                current = await self._lock_claim(work.id)
                if current is None:
                    return VerificationWorkStatus.CLAIMED
                current.status = VerificationWorkStatus.PENDING.value
                current.lease_owner = None
                current.lease_expires_at = None
                current.last_error_code = "SOURCE_UNAVAILABLE"
                current.next_attempt_at = moment + timedelta(
                    seconds=_compute_backoff(work.attempt_count)
                )
                await self.db.flush()
            return VerificationWorkStatus.PENDING

        descriptor = adapter._resolve_source_descriptor()
        observation = RegistryObservation(
            resource_type=resource_type,
            source_id=descriptor.source_id,
            adapter_version=descriptor.adapter_version,
            observed_at=datetime.now(timezone.utc),
            lookup_purpose=VerificationEvidenceLookupPurpose(work.lookup_purpose),
            outcome=VerificationEvidenceOutcome.SOURCE_UNAVAILABLE,
            identity_binding_result=VerificationIdentityBindingResult.NOT_EVALUATED,
        )
        envelope = ValidatedRegistryLookupEnvelope(
            invocation=invocation,
            observation=observation,
        )
        return await self._apply_and_terminalize(
            work.id,
            envelope,
            invocation,
            VerificationWorkStatus.EXHAUSTED,
            moment,
            "SOURCE_UNAVAILABLE",
        )

    async def _apply_and_terminalize(
        self,
        work_id: UUID,
        envelope: ValidatedRegistryLookupEnvelope,
        invocation: RegistryLookupInvocation,
        terminal: VerificationWorkStatus,
        completed_at: datetime,
        error_code: str | None,
    ) -> VerificationWorkStatus:
        """Atomically apply 5E under a revalidated lease and terminalize work."""
        async with self._transaction():
            work = await self._lock_claim(work_id)
            if work is None:
                return VerificationWorkStatus.CLAIMED
            if (
                work.id != invocation.invocation_id
                or self._invocation_timestamp(work) != invocation.invoked_at
            ):
                raise ProviderVerificationWorkerError("WORK_INVOCATION_MISMATCH")
            application = ProviderVerificationApplicationService(
                self.db,
                source_policies=self.source_policies,
                automation_enabled=self._automation_enabled,
            )
            try:
                result = await application.apply_verification_observation(envelope)
            except Exception as exc:
                code = getattr(exc, "code", "APPLICATION_FAILURE")
                work.status = (
                    VerificationWorkStatus.CANCELLED_STALE.value
                    if code == "LIFECYCLE_VERSION_CONFLICT"
                    else VerificationWorkStatus.FAILED_TERMINAL.value
                )
                work.last_error_code = code
                work.completed_at = completed_at
                work.lease_owner = None
                work.lease_expires_at = None
                await self.db.flush()
                return VerificationWorkStatus(work.status)
            await self._terminalize_applied_work(
                work,
                terminal=terminal,
                evidence_id=result.evidence_id,
                completed_at=completed_at,
                error_code=error_code,
            )
            return terminal

    async def _terminalize_applied_work(
        self,
        work: ProviderVerificationWork,
        *,
        terminal: VerificationWorkStatus,
        evidence_id: UUID,
        completed_at: datetime,
        error_code: str | None,
    ) -> None:
        """Stage terminal work state in the same outer application transaction."""
        work.status = terminal.value
        work.result_evidence_id = evidence_id
        work.completed_at = completed_at
        work.last_error_code = error_code
        work.lease_owner = None
        work.lease_expires_at = None
        await self.db.flush()

    async def _terminalize_without_application(
        self,
        work_id: UUID,
        status: VerificationWorkStatus,
        completed_at: datetime,
        error_code: str,
    ) -> VerificationWorkStatus:
        async with self._transaction():
            work = await self._lock_claim(work_id)
            if work is None:
                return VerificationWorkStatus.CLAIMED
            work.status = status.value
            work.last_error_code = error_code
            work.completed_at = completed_at
            work.lease_owner = None
            work.lease_expires_at = None
            await self.db.flush()
            return status


def _aware(value: datetime | None) -> datetime:
    moment = value or datetime.now(timezone.utc)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
