"""Unit tests for ProviderVerificationSchedulerService (Phase 5F).

All tests use in-memory mocks and do not require a database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.models.provider import (
    FacilityVerification,
    FacilityVerificationStatus,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderVerificationWork,
    VerificationWorkStatus,
)
from app.services.provider_verification_application import (
    SourceAutomationPolicy,
    SourceAutomationPolicyRegistry,
)
from app.services.provider_verification_registry import RegistryResourceType
from app.services.provider_verification_scheduler import (
    ProviderVerificationSchedulerService,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)
_PROF_ID = uuid.uuid4()
_FAC_ID = uuid.uuid4()
_FAC_FACILITY_ID = uuid.uuid4()

# A simple policy that enables automation for professionals.
_PROF_POLICY = SourceAutomationPolicy(
    source_id="NMC_REGISTRY",
    resource_type=RegistryResourceType.PROFESSIONAL,
    registration_authority_code="NMC",
    approved_adapter_version="1.0.0",
    automation_enabled=True,
    recheck_interval_seconds=2592000,  # 30 days
)
_FAC_POLICY = SourceAutomationPolicy(
    source_id="NHA_REGISTRY",
    resource_type=RegistryResourceType.FACILITY,
    registration_authority_code="NHA",
    approved_adapter_version="1.0.0",
    automation_enabled=True,
    recheck_interval_seconds=2592000,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prof(
    *,
    status: str = ProfessionalVerificationStatus.VERIFIED.value,
    next_review_at: datetime | None = None,
    server_provenance_evidence_id: UUID | None = None,
    registration_authority_code: str = "NMC",
    registration_number_normalized: str = "NMC001",
    version: int = 1,
) -> ProfessionalVerification:
    p = MagicMock(spec=ProfessionalVerification)
    p.id = _PROF_ID
    p.status = status
    p.next_review_at = next_review_at
    p.server_provenance_evidence_id = server_provenance_evidence_id
    p.registration_authority_code = registration_authority_code
    p.registration_number_normalized = registration_number_normalized
    p.version = version
    return p


def _make_fac(
    *,
    status: str = FacilityVerificationStatus.VERIFIED.value,
    next_review_at: datetime | None = None,
    server_provenance_evidence_id: UUID | None = None,
    registration_authority_code: str = "NHA",
    registration_number_normalized: str = "NHA001",
    facility_id: UUID | None = None,
    version: int = 1,
) -> FacilityVerification:
    f = MagicMock(spec=FacilityVerification)
    f.id = _FAC_ID
    f.status = status
    f.next_review_at = next_review_at
    f.server_provenance_evidence_id = server_provenance_evidence_id
    f.registration_authority_code = registration_authority_code
    f.registration_number_normalized = registration_number_normalized
    f.facility_id = facility_id or _FAC_FACILITY_ID
    f.version = version
    return f


def _make_db(
    *,
    prof_rows: list | None = None,
    fac_rows: list | None = None,
    has_open_review: bool = False,
    has_active_work: bool = False,
) -> AsyncMock:
    """Build a mock AsyncSession that returns the given rows for scheduler queries."""
    db = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    db.in_transaction = MagicMock(return_value=False)
    db.begin = MagicMock(return_value=transaction)
    db.begin_nested = MagicMock(return_value=transaction)

    # We patch _has_open_review_work and _has_active_work directly on the service
    # rather than mocking the DB query chain here; keeping this simple.

    # Mock execute() to return the prof/fac rows
    _prof_result = MagicMock()
    _prof_result.scalars.return_value.all.return_value = prof_rows or []
    _fac_result = MagicMock()
    _fac_result.scalars.return_value.all.return_value = fac_rows or []

    # Return prof rows for first call, fac rows for second
    db.execute.side_effect = [_prof_result, _fac_result]

    # db.add / db.flush are noops
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=None)

    return db


def _make_svc(
    db: AsyncMock,
    *,
    policies: list[SourceAutomationPolicy] | None = None,
    automation_enabled: bool = True,
) -> ProviderVerificationSchedulerService:
    registry = SourceAutomationPolicyRegistry(policies or [_PROF_POLICY, _FAC_POLICY])
    return ProviderVerificationSchedulerService(
        db,
        source_policies=registry,
        automation_enabled=automation_enabled,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKillSwitch:
    """When automation is globally disabled, sweep returns []."""

    @pytest.mark.asyncio
    async def test_kill_switch_off_returns_empty(self) -> None:
        db = _make_db()
        svc = _make_svc(db, automation_enabled=False)
        result = await svc.sweep_due_verifications(now=_NOW)
        assert result == []
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_kill_switch_callable_off_returns_empty(self) -> None:
        db = _make_db()
        registry = SourceAutomationPolicyRegistry([_PROF_POLICY])
        svc = ProviderVerificationSchedulerService(
            db, source_policies=registry, automation_enabled=lambda: False
        )
        result = await svc.sweep_due_verifications(now=_NOW)
        assert result == []


class TestOpenReviewWorkBlocking:
    """When open review work exists, that credential is skipped."""

    @pytest.mark.asyncio
    async def test_open_review_work_blocks_scheduling(self) -> None:
        prof = _make_prof(
            next_review_at=_NOW - timedelta(days=1),
        )
        db = _make_db(prof_rows=[prof])

        svc = _make_svc(db)
        # Patch internal helper to report open review exists
        svc._has_open_review_work = AsyncMock(return_value=True)
        svc._has_active_work = AsyncMock(return_value=False)
        svc._resolve_applicable_source_policy = AsyncMock(return_value=_PROF_POLICY)

        result = await svc.sweep_due_verifications(now=_NOW)
        assert result == []


class TestActiveWorkBlocking:
    """When active (PENDING or CLAIMED) work already exists, that credential is skipped."""

    @pytest.mark.asyncio
    async def test_active_work_blocks_scheduling(self) -> None:
        prof = _make_prof(
            next_review_at=_NOW - timedelta(days=1),
        )
        db = _make_db(prof_rows=[prof])

        svc = _make_svc(db)
        svc._has_open_review_work = AsyncMock(return_value=False)
        svc._has_active_work = AsyncMock(return_value=True)
        svc._resolve_applicable_source_policy = AsyncMock(return_value=_PROF_POLICY)

        result = await svc.sweep_due_verifications(now=_NOW)
        assert result == []


class TestNotYetDueSkipped:
    """A VERIFIED record whose next_review_at is in the future is skipped."""

    @pytest.mark.asyncio
    async def test_verified_not_due_is_skipped(self) -> None:
        future = _NOW + timedelta(days=10)
        prof = _make_prof(
            status=ProfessionalVerificationStatus.VERIFIED.value,
            next_review_at=future,
        )
        db = _make_db(prof_rows=[prof])

        svc = _make_svc(db)
        svc._has_open_review_work = AsyncMock(return_value=False)
        svc._has_active_work = AsyncMock(return_value=False)
        svc._resolve_applicable_source_policy = AsyncMock(return_value=_PROF_POLICY)

        # Patch enqueue_audit_event to a noop so we don't hit missing audit_outbox
        with patch(
            "app.services.provider_verification_scheduler.enqueue_audit_event",
            new=AsyncMock(),
        ):
            result = await svc.sweep_due_verifications(now=_NOW)

        assert result == []


class TestVerifiedAndDueCreatesWork:
    """A VERIFIED record past its next_review_at should trigger a MARK_RECHECK_DUE transition
    and create a ProviderVerificationWork row."""

    @pytest.mark.asyncio
    async def test_verified_and_due_creates_work(self) -> None:
        past = _NOW - timedelta(days=1)
        prof = _make_prof(
            status=ProfessionalVerificationStatus.VERIFIED.value,
            next_review_at=past,
            version=3,
        )
        db = _make_db(prof_rows=[prof])

        # Make plan_professional_transition return a minimal plan
        mock_plan = MagicMock()
        mock_plan.updates = []
        mock_plan.clears = []
        mock_plan.next_version = 4

        svc = _make_svc(db)
        svc._has_open_review_work = AsyncMock(return_value=False)
        svc._has_active_work = AsyncMock(return_value=False)
        svc._resolve_applicable_source_policy = AsyncMock(return_value=_PROF_POLICY)

        created_works: list = []

        def capture_add(obj: Any) -> None:
            if isinstance(obj, ProviderVerificationWork):
                created_works.append(obj)

        db.add = MagicMock(side_effect=capture_add)

        with (
            patch(
                "app.services.provider_verification_scheduler.plan_professional_transition",
                return_value=mock_plan,
            ),
            patch(
                "app.services.provider_verification_scheduler.enqueue_audit_event",
                new=AsyncMock(),
            ),
        ):
            result = await svc.sweep_due_verifications(now=_NOW)

        assert len(result) == 1
        work = result[0]
        assert work.professional_verification_id == _PROF_ID
        assert work.status == VerificationWorkStatus.PENDING.value
        assert work.source_id == _PROF_POLICY.source_id
        assert work.registration_authority_code == "NMC"
        assert work.scheduler_reason == "SCHEDULED_REVIEW_DUE"
        assert work.attempt_count == 0
        assert work.max_attempts == 5


class TestRecheckDueBootstrap:
    """A record already in RECHECK_DUE creates work with scheduler_reason=RECHECK_DUE_BOOTSTRAP."""

    @pytest.mark.asyncio
    async def test_recheck_due_bootstrap_creates_work(self) -> None:
        prof = _make_prof(
            status=ProfessionalVerificationStatus.RECHECK_DUE.value,
            next_review_at=None,
            version=2,
        )
        db = _make_db(prof_rows=[prof])

        svc = _make_svc(db)
        svc._has_open_review_work = AsyncMock(return_value=False)
        svc._has_active_work = AsyncMock(return_value=False)
        svc._resolve_applicable_source_policy = AsyncMock(return_value=_PROF_POLICY)

        created_works: list = []

        def capture_add(obj: Any) -> None:
            if isinstance(obj, ProviderVerificationWork):
                created_works.append(obj)

        db.add = MagicMock(side_effect=capture_add)

        with patch(
            "app.services.provider_verification_scheduler.enqueue_audit_event",
            new=AsyncMock(),
        ):
            result = await svc.sweep_due_verifications(now=_NOW)

        assert len(result) == 1
        assert result[0].scheduler_reason == "RECHECK_DUE_BOOTSTRAP"


class TestNoPolicySkips:
    """When no source policy is applicable, the credential is skipped."""

    @pytest.mark.asyncio
    async def test_no_policy_skips_credential(self) -> None:
        prof = _make_prof(
            next_review_at=_NOW - timedelta(days=1),
        )
        db = _make_db(prof_rows=[prof])

        svc = _make_svc(db)
        svc._has_open_review_work = AsyncMock(return_value=False)
        svc._has_active_work = AsyncMock(return_value=False)
        # Policy returns None — no applicable policy
        svc._resolve_applicable_source_policy = AsyncMock(return_value=None)

        with patch(
            "app.services.provider_verification_scheduler.enqueue_audit_event",
            new=AsyncMock(),
        ):
            result = await svc.sweep_due_verifications(now=_NOW)

        assert result == []


class TestFacilityRecheckRequired:
    """A RECHECK_REQUIRED facility creates work with scheduler_reason=RECHECK_REQUIRED_BOOTSTRAP."""

    @pytest.mark.asyncio
    async def test_facility_recheck_required_creates_work(self) -> None:
        fac = _make_fac(
            status=FacilityVerificationStatus.RECHECK_REQUIRED.value,
            version=2,
        )
        db = _make_db(fac_rows=[fac])

        svc = _make_svc(db)
        svc._has_open_review_work = AsyncMock(return_value=False)
        svc._has_active_work = AsyncMock(return_value=False)
        svc._resolve_applicable_source_policy = AsyncMock(return_value=_FAC_POLICY)

        created_works: list = []

        def capture_add(obj: Any) -> None:
            if isinstance(obj, ProviderVerificationWork):
                created_works.append(obj)

        db.add = MagicMock(side_effect=capture_add)

        with patch(
            "app.services.provider_verification_scheduler.enqueue_audit_event",
            new=AsyncMock(),
        ):
            result = await svc.sweep_due_verifications(now=_NOW)

        assert len(result) == 1
        assert result[0].scheduler_reason == "RECHECK_REQUIRED_BOOTSTRAP"
        assert result[0].facility_verification_id == _FAC_ID
        assert result[0].professional_verification_id is None


class TestUniqueAutomationPolicyForAuthority:
    """Scheduling authority lookup is exact and fails closed on ambiguity."""

    def test_returns_enabled_matching_policy(self) -> None:
        registry = SourceAutomationPolicyRegistry([_PROF_POLICY])
        result = registry.get_unique_automation_policy_for_authority(
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="NMC",
        )
        assert result is _PROF_POLICY

    def test_returns_none_when_disabled(self) -> None:
        disabled = SourceAutomationPolicy(
            source_id="NMC_REGISTRY", automation_enabled=False
        )
        registry = SourceAutomationPolicyRegistry([disabled])
        result = registry.get_unique_automation_policy_for_authority(
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="NMC",
        )
        assert result is None

    def test_returns_none_for_wrong_resource_type(self) -> None:
        registry = SourceAutomationPolicyRegistry([_PROF_POLICY])
        result = registry.get_unique_automation_policy_for_authority(
            resource_type=RegistryResourceType.FACILITY,
            registration_authority_code="NMC",
        )
        assert result is None

    def test_returns_none_for_wrong_authority(self) -> None:
        registry = SourceAutomationPolicyRegistry([_PROF_POLICY])
        result = registry.get_unique_automation_policy_for_authority(
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="WRONG",
        )
        assert result is None

    def test_returns_none_on_empty_registry(self) -> None:
        registry = SourceAutomationPolicyRegistry([])
        result = registry.get_unique_automation_policy_for_authority(
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="NMC",
        )
        assert result is None

    def test_returns_none_when_two_enabled_policies_match(self) -> None:
        alternate = SourceAutomationPolicy(
            source_id="NMC_REGISTRY_BACKUP",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="NMC",
            approved_adapter_version="1.0.0",
            automation_enabled=True,
            recheck_interval_seconds=2592000,
        )
        registry = SourceAutomationPolicyRegistry([_PROF_POLICY, alternate])
        assert (
            registry.get_unique_automation_policy_for_authority(
                resource_type=RegistryResourceType.PROFESSIONAL,
                registration_authority_code="NMC",
            )
            is None
        )
