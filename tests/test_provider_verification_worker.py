"""Unit tests for ProviderVerificationWorkerService (Phase 5F).

All tests use in-memory mocks and synthetic adapters; no database required.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.provider import (
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderVerificationWork,
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
    VerificationWorkStatus,
)
from app.services.provider_verification_application import (
    SourceAutomationPolicy,
    SourceAutomationPolicyRegistry,
    VerificationApplicationError,
)
from app.services.provider_verification_registry import (
    RegistryAdapterContractError,
    RegistryObservationInvalidError,
    RegistryRequestInvalidError,
    RegistryResourceType,
    RegistrySourceDescriptor,
    RegistryTransientUnavailableError,
    RegistryUnsupportedResourceError,
    SyntheticRegistryAdapter,
)
from app.services.provider_verification_worker import (
    ProviderVerificationWorkerService,
    _compute_backoff,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)
_PROF_ID = uuid.uuid4()
_WORK_ID = uuid.uuid4()
_LAST_WORK: ProviderVerificationWork | None = None

_PROF_POLICY = SourceAutomationPolicy(
    source_id="NMC_REGISTRY",
    resource_type=RegistryResourceType.PROFESSIONAL,
    registration_authority_code="NMC",
    approved_adapter_version="1.0.0",
    automation_enabled=True,
    recheck_interval_seconds=2592000,
)

_DESCRIPTOR = RegistrySourceDescriptor(
    source_id="NMC_REGISTRY",
    adapter_version="1.0.0",
    supported_resource_types=(RegistryResourceType.PROFESSIONAL,),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work(
    *,
    status: str = VerificationWorkStatus.CLAIMED.value,
    source_id: str = "NMC_REGISTRY",
    attempt_count: int = 0,
    max_attempts: int = 3,
    expected_resource_version: int = 1,
) -> ProviderVerificationWork:
    global _LAST_WORK
    w = MagicMock(spec=ProviderVerificationWork)
    w.id = _WORK_ID
    w.status = status
    w.source_id = source_id
    w.adapter_version = "1.0.0"
    w.registration_authority_code = "NMC"
    w.registration_number_normalized = "NMC001"
    w.lookup_purpose = VerificationEvidenceLookupPurpose.RECHECK.value
    w.expected_resource_version = expected_resource_version
    w.attempt_count = attempt_count
    w.max_attempts = max_attempts
    w.professional_verification_id = _PROF_ID
    w.facility_verification_id = None
    w.next_attempt_at = _NOW - timedelta(seconds=1)
    w.lease_owner = "worker:test"
    w.lease_expires_at = _NOW + timedelta(seconds=60)
    w.created_at = _NOW - timedelta(minutes=1)
    _LAST_WORK = w
    return w


def _make_prof(*, version: int = 1) -> ProfessionalVerification:
    p = MagicMock(spec=ProfessionalVerification)
    p.id = _PROF_ID
    p.status = ProfessionalVerificationStatus.RECHECK_DUE.value
    p.version = version
    p.registration_authority_code = "NMC"
    p.registration_number_normalized = "NMC001"
    p.server_provenance_evidence_id = None
    return p


def _make_db(prof: ProfessionalVerification | None = None) -> AsyncMock:
    db = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    db.in_transaction = MagicMock(return_value=False)
    db.begin = MagicMock(return_value=transaction)
    db.begin_nested = MagicMock(return_value=transaction)
    db.get = AsyncMock(return_value=prof or _make_prof())
    db.add = MagicMock()
    db.flush = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = _LAST_WORK
    db.execute = AsyncMock(return_value=result)
    return db


def _make_svc(
    db: AsyncMock,
    *,
    adapters: dict | None = None,
    automation_enabled: bool = True,
    policies: list | None = None,
) -> ProviderVerificationWorkerService:
    registry = SourceAutomationPolicyRegistry(policies or [_PROF_POLICY])
    return ProviderVerificationWorkerService(
        db,
        worker_id="worker:test",
        adapters=adapters
        or {
            "NMC_REGISTRY": SyntheticRegistryAdapter(
                descriptor=_DESCRIPTOR,
                default_professional_outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
                default_binding_result=VerificationIdentityBindingResult.MATCHED,
            )
        },
        source_policies=registry,
        automation_enabled=automation_enabled,
    )


# ---------------------------------------------------------------------------
# Backoff helper tests
# ---------------------------------------------------------------------------


class TestComputeBackoff:
    def test_first_attempt_uses_base(self) -> None:
        assert _compute_backoff(1) == 30

    def test_second_attempt_doubles(self) -> None:
        assert _compute_backoff(2) == 60

    def test_caps_at_max(self) -> None:
        # At attempt 20 the raw value would be enormous; must be capped.
        assert _compute_backoff(20) == 3600

    def test_zero_attempt_does_not_crash(self) -> None:
        val = _compute_backoff(0)
        assert val >= 30


# ---------------------------------------------------------------------------
# claim_work_batch tests
# ---------------------------------------------------------------------------


class TestClaimWorkBatch:
    @pytest.mark.asyncio
    async def test_kill_switch_off_returns_empty(self) -> None:
        db = _make_db()
        svc = _make_svc(db, automation_enabled=False)
        result = await svc.claim_work_batch(now=_NOW)
        assert result == []
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_claims_pending_rows(self) -> None:
        work = _make_work(status=VerificationWorkStatus.PENDING.value)
        db = _make_db()

        # Mock execute to return the work item
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [work]
        db.execute = AsyncMock(return_value=mock_result)

        svc = _make_svc(db)
        claimed = await svc.claim_work_batch(now=_NOW)

        assert len(claimed) == 1
        assert claimed[0].status == VerificationWorkStatus.CLAIMED.value
        assert claimed[0].lease_owner == "worker:test"
        assert claimed[0].lease_expires_at is not None

    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty(self) -> None:
        db = _make_db()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=mock_result)

        svc = _make_svc(db)
        claimed = await svc.claim_work_batch(now=_NOW)
        assert claimed == []
        db.flush.assert_not_called()


# ---------------------------------------------------------------------------
# process_work_item — kill switch / policy disabled
# ---------------------------------------------------------------------------


class TestProcessCancelledPolicy:
    @pytest.mark.asyncio
    async def test_global_kill_switch_marks_cancelled_policy(self) -> None:
        work = _make_work()
        db = _make_db()
        svc = _make_svc(db, automation_enabled=False)

        result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.CANCELLED_POLICY
        assert work.status == VerificationWorkStatus.CANCELLED_POLICY.value

    @pytest.mark.asyncio
    async def test_source_policy_disabled_marks_cancelled_policy(self) -> None:
        disabled_policy = SourceAutomationPolicy(
            source_id="NMC_REGISTRY", automation_enabled=False
        )
        work = _make_work()
        db = _make_db()
        svc = _make_svc(db, policies=[disabled_policy])

        result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.CANCELLED_POLICY
        assert work.status == VerificationWorkStatus.CANCELLED_POLICY.value


# ---------------------------------------------------------------------------
# process_work_item — no adapter configured
# ---------------------------------------------------------------------------


class TestProcessNoAdapter:
    @pytest.mark.asyncio
    async def test_no_adapter_marks_failed_terminal(self) -> None:
        work = _make_work(source_id="UNKNOWN_REGISTRY")
        db = _make_db()
        svc = _make_svc(db, adapters={})  # No adapters

        # We still need a policy for UNKNOWN_REGISTRY so we pass the kill-switch
        unknown_policy = SourceAutomationPolicy(
            source_id="UNKNOWN_REGISTRY",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="NMC",
            approved_adapter_version="1.0.0",
            automation_enabled=True,
            recheck_interval_seconds=86400,
        )
        svc.source_policies = SourceAutomationPolicyRegistry([unknown_policy])

        result = await svc.process_work_item(work, now=_NOW)
        assert result == VerificationWorkStatus.FAILED_TERMINAL


# ---------------------------------------------------------------------------
# process_work_item — transient failure rescheduling
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# process_work_item — transient failure rescheduling
# ---------------------------------------------------------------------------


class TestProcessTransientFailure:
    @pytest.mark.asyncio
    async def test_transient_failure_reschedules_with_backoff(self) -> None:
        work = _make_work(attempt_count=0, max_attempts=3)
        db = _make_db()

        # Adapter that raises RegistryTransientUnavailableError
        failing_adapter = AsyncMock(
            spec=["lookup_professional", "_resolve_source_descriptor"]
        )
        failing_adapter.lookup_professional = AsyncMock(
            side_effect=RegistryTransientUnavailableError("transient network error")
        )
        failing_adapter._resolve_source_descriptor = MagicMock(return_value=_DESCRIPTOR)

        svc = _make_svc(db, adapters={"NMC_REGISTRY": failing_adapter})

        with patch(
            "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
            side_effect=RegistryTransientUnavailableError("transient"),
        ):
            result = await svc.process_work_item(work, now=_NOW)

        # Should be rescheduled (PENDING), not terminal
        assert result == VerificationWorkStatus.PENDING
        assert work.status == VerificationWorkStatus.PENDING.value
        # next_attempt_at should be in the future
        assert work.next_attempt_at > _NOW

    @pytest.mark.asyncio
    async def test_transient_failure_increments_attempt_count(self) -> None:
        work = _make_work(attempt_count=0, max_attempts=5)
        db = _make_db()

        svc = _make_svc(db)

        with patch(
            "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
            side_effect=RegistryTransientUnavailableError("transient"),
        ):
            await svc.process_work_item(work, now=_NOW)

        # attempt_count should have been incremented before the call
        assert work.attempt_count == 1


# ---------------------------------------------------------------------------
# process_work_item — exhaustion
# ---------------------------------------------------------------------------


class TestProcessExhausted:
    @pytest.mark.asyncio
    async def test_exhaustion_after_max_attempts(self) -> None:
        # attempt_count=max_attempts-1 so this will be the last attempt
        work = _make_work(attempt_count=2, max_attempts=3)
        db = _make_db()

        svc = _make_svc(db)

        # Mock apply_verification_observation so it doesn't try to hit DB
        with (
            patch(
                "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
                side_effect=RegistryTransientUnavailableError("source down"),
            ),
            patch(
                "app.services.provider_verification_worker.ProviderVerificationApplicationService"
            ) as mock_app_cls,
        ):
            mock_app = AsyncMock()
            mock_app.apply_verification_observation = AsyncMock(
                return_value=MagicMock()
            )
            mock_app_cls.return_value = mock_app

            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.EXHAUSTED
        assert work.status == VerificationWorkStatus.EXHAUSTED.value
        # Adapter's _resolve_source_descriptor must have been called to build exhaustion obs
        mock_app.apply_verification_observation.assert_called_once()


# ---------------------------------------------------------------------------
# process_work_item — non-retryable failures (Section 12 matrix)
# ---------------------------------------------------------------------------


class TestProcessNonRetryableFailures:
    @pytest.mark.asyncio
    async def test_contract_error_marks_failed_terminal_with_zero_5e_calls(
        self,
    ) -> None:
        work = _make_work(attempt_count=0, max_attempts=5)
        db = _make_db()
        svc = _make_svc(db)

        with (
            patch(
                "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
                side_effect=RegistryAdapterContractError("contract violation"),
            ),
            patch(
                "app.services.provider_verification_worker.ProviderVerificationApplicationService"
            ) as mock_app_cls,
        ):
            mock_app = AsyncMock()
            mock_app_cls.return_value = mock_app

            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.FAILED_TERMINAL
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
        assert work.last_error_code == "REGISTRY_CONTRACT_ERROR"
        assert not hasattr(work, "last_error_message")
        mock_app.apply_verification_observation.assert_not_called()

    @pytest.mark.asyncio
    async def test_observation_invalid_error_marks_failed_terminal_with_zero_5e_calls(
        self,
    ) -> None:
        work = _make_work(attempt_count=0, max_attempts=5)
        db = _make_db()
        svc = _make_svc(db)

        with (
            patch(
                "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
                side_effect=RegistryObservationInvalidError(
                    "invalid observation bounds"
                ),
            ),
            patch(
                "app.services.provider_verification_worker.ProviderVerificationApplicationService"
            ) as mock_app_cls,
        ):
            mock_app = AsyncMock()
            mock_app_cls.return_value = mock_app

            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.FAILED_TERMINAL
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
        assert work.last_error_code == "REGISTRY_OBSERVATION_INVALID"
        assert not hasattr(work, "last_error_message")
        mock_app.apply_verification_observation.assert_not_called()

    @pytest.mark.asyncio
    async def test_request_invalid_error_marks_failed_terminal(self) -> None:
        work = _make_work(attempt_count=0, max_attempts=5)
        db = _make_db()
        svc = _make_svc(db)

        with patch(
            "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
            side_effect=RegistryRequestInvalidError("bad request"),
        ):
            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.FAILED_TERMINAL
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
        assert work.last_error_code == "REGISTRY_REQUEST_INVALID"
        assert not hasattr(work, "last_error_message")

    @pytest.mark.asyncio
    async def test_unsupported_resource_error_marks_failed_terminal(self) -> None:
        work = _make_work(attempt_count=0, max_attempts=5)
        db = _make_db()
        svc = _make_svc(db)

        with patch(
            "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
            side_effect=RegistryUnsupportedResourceError("unsupported resource"),
        ):
            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.FAILED_TERMINAL
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
        assert work.last_error_code == "REGISTRY_UNSUPPORTED_RESOURCE"
        assert not hasattr(work, "last_error_message")

    @pytest.mark.asyncio
    async def test_generic_runtime_error_marks_failed_terminal_no_retry_zero_5e_calls(
        self,
    ) -> None:
        work = _make_work(attempt_count=0, max_attempts=5)
        db = _make_db()
        svc = _make_svc(db)

        with (
            patch(
                "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
                side_effect=RuntimeError("Authorization: Bearer SUPER_SECRET_TOKEN"),
            ),
            patch(
                "app.services.provider_verification_worker.ProviderVerificationApplicationService"
            ) as mock_app_cls,
        ):
            mock_app = AsyncMock()
            mock_app_cls.return_value = mock_app

            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.FAILED_TERMINAL
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
        assert work.last_error_code == "WORKER_INTERNAL_ERROR"
        assert "SUPER_SECRET_TOKEN" not in getattr(work, "last_error_code", "")
        assert not hasattr(work, "last_error_message")
        mock_app.apply_verification_observation.assert_not_called()


# ---------------------------------------------------------------------------
# process_work_item — version conflict → CANCELLED_STALE
# ---------------------------------------------------------------------------


class TestProcessVersionConflict:
    @pytest.mark.asyncio
    async def test_version_conflict_marks_cancelled_stale(self) -> None:
        work = _make_work(expected_resource_version=1)
        db = _make_db()
        svc = _make_svc(db)

        with (
            patch(
                "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "app.services.provider_verification_worker.ProviderVerificationApplicationService"
            ) as mock_app_cls,
        ):
            mock_app = AsyncMock()
            mock_app.apply_verification_observation = AsyncMock(
                side_effect=VerificationApplicationError("LIFECYCLE_VERSION_CONFLICT")
            )
            mock_app_cls.return_value = mock_app

            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.CANCELLED_STALE
        assert work.status == VerificationWorkStatus.CANCELLED_STALE.value


# ---------------------------------------------------------------------------
# process_work_item — successful completion
# ---------------------------------------------------------------------------


class TestProcessCompleted:
    @pytest.mark.asyncio
    async def test_successful_observation_marks_completed(self) -> None:
        work = _make_work(attempt_count=0, max_attempts=5)
        db = _make_db()
        svc = _make_svc(db)

        mock_envelope = MagicMock()
        mock_envelope.observation = MagicMock()
        mock_envelope.observation.source_id = "NMC_REGISTRY"
        mock_envelope.observation.outcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE

        with (
            patch(
                "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
                new=AsyncMock(return_value=mock_envelope),
            ),
            patch(
                "app.services.provider_verification_worker.ProviderVerificationApplicationService"
            ) as mock_app_cls,
        ):
            mock_app = AsyncMock()
            mock_app.apply_verification_observation = AsyncMock(
                return_value=MagicMock()
            )
            mock_app_cls.return_value = mock_app

            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.COMPLETED
        assert work.status == VerificationWorkStatus.COMPLETED.value
        assert work.lease_owner is None
        assert work.lease_expires_at is None


# ---------------------------------------------------------------------------
# process_work_item — unexpected application error
# ---------------------------------------------------------------------------


class TestProcessUnexpectedError:
    @pytest.mark.asyncio
    async def test_unexpected_application_error_marks_failed_terminal(self) -> None:
        work = _make_work()
        db = _make_db()
        svc = _make_svc(db)

        mock_envelope = MagicMock()
        mock_envelope.observation = MagicMock()
        mock_envelope.observation.source_id = "NMC_REGISTRY"
        mock_envelope.observation.outcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE

        with (
            patch(
                "app.services.provider_verification_worker.execute_lookup_and_create_envelope",
                new=AsyncMock(return_value=mock_envelope),
            ),
            patch(
                "app.services.provider_verification_worker.ProviderVerificationApplicationService"
            ) as mock_app_cls,
        ):
            mock_app = AsyncMock()
            mock_app.apply_verification_observation = AsyncMock(
                side_effect=RuntimeError("unexpected")
            )
            mock_app_cls.return_value = mock_app

            result = await svc.process_work_item(work, now=_NOW)

        assert result == VerificationWorkStatus.FAILED_TERMINAL
        assert work.status == VerificationWorkStatus.FAILED_TERMINAL.value
