import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from pydantic import BaseModel
import pytest

from app.models.provider import (
    FacilityVerification,
    ProfessionalVerification,
    ProviderHospitalAffiliation,
    ProviderTrustVerificationEvidence,
    VerificationEvidenceOrigin,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
    VerificationSourceFailureReason,
)
from app.services.provider_trust_lifecycle import (
    AffiliationTransitionCommand,
    FacilityTransitionCommand,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
)
from app.services.provider_trust_lifecycle_application import (
    ProviderTrustLifecycleApplicationError,
    ProviderTrustLifecycleApplicationService,
    _request_hash,
)
import app.api.v2.provider_trust_permission_routes as ptpr
import app.api.v2.provider_trust_routes as ptr


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _professional_facts() -> ProfessionalTransitionFacts:
    return ProfessionalTransitionFacts(
        registration_authority_code="AUTH",
        registration_number_normalized="REG-1",
    )


def test_request_hash_is_canonical_and_binds_semantic_request() -> None:
    actor, resource = uuid4(), uuid4()
    first = _request_hash(
        actor_id=actor,
        lifecycle_type="professional",
        resource_id=resource,
        command=ProfessionalTransitionCommand.SUBMIT,
        expected_version=1,
        facts=_professional_facts(),
    )
    assert first == _request_hash(
        actor_id=actor,
        lifecycle_type="professional",
        resource_id=resource,
        command=ProfessionalTransitionCommand.SUBMIT,
        expected_version=1,
        facts=_professional_facts(),
    )
    assert first != _request_hash(
        actor_id=actor,
        lifecycle_type="professional",
        resource_id=resource,
        command=ProfessionalTransitionCommand.SUBMIT,
        expected_version=2,
        facts=replace(_professional_facts(), registration_number_normalized="REG-2"),
    )


def test_affiliation_allowlist_rejects_roles_and_identity_mutation() -> None:
    service = ProviderTrustLifecycleApplicationService(db=None)  # type: ignore[arg-type]
    target = ProviderHospitalAffiliation(
        id=uuid4(), provider_id=uuid4(), hospital_id=uuid4()
    )
    plan = type(
        "Plan",
        (),
        {
            "updates": (type("Update", (), {"field": "roles", "value": ["admin"]})(),),
            "clears": frozenset(),
            "new_state": "ACTIVE",
            "next_version": 2,
        },
    )()
    with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
        service._apply_plan("affiliation", target, plan)  # type: ignore[arg-type]
    assert exc.value.code == "TRANSACTION_INTEGRITY_FAILURE"


def test_application_service_has_no_http_or_grant_management_surface() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app/services/provider_trust_lifecycle_application.py"
    ).read_text(encoding="utf-8")
    assert "fastapi" not in source.lower()
    assert "@router" not in source
    assert "INSERT INTO provider_trust_permission_grant" not in source
    assert "UPDATE provider_trust_permission_grant" not in source
    assert "DELETE FROM provider_trust_permission_grant" not in source


def test_invalid_expected_version_is_rejected_before_transaction():
    service = ProviderTrustLifecycleApplicationService(db=None)  # type: ignore[arg-type]
    with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
        import asyncio

        asyncio.run(
            service.apply_professional(
                actor_id=uuid4(),
                authentication=None,  # type: ignore[arg-type]
                resource_id=uuid4(),
                command=ProfessionalTransitionCommand.SUBMIT,
                facts=_professional_facts(),
                expected_version=0,
                idempotency_key="valid-key-123",
                now=NOW,
            )
        )
    assert exc.value.code == "INVALID_REQUEST"


def test_route_mark_recheck_due_derives_locked_facts_and_never_grants_grace() -> None:
    """The Phase-3F flag discards even a trusted-facts-shaped caller object."""
    service = ProviderTrustLifecycleApplicationService(db=None)  # type: ignore[arg-type]
    target = ProfessionalVerification(
        id=uuid4(),
        provider_id=uuid4(),
        status="VERIFIED",
        version=7,
        previous_verification_valid=True,
        registration_valid_until=NOW + timedelta(days=30),
    )

    plan = service._plan(
        "professional",
        target,
        ProfessionalTransitionCommand.MARK_RECHECK_DUE,
        ProfessionalTransitionFacts(
            previous_verification_valid=False,
            recheck_failure_reason=VerificationSourceFailureReason.SOURCE_UNAVAILABLE,
            grace_expires_at=NOW + timedelta(hours=12),
            recheck_attempted_at=NOW + timedelta(days=1),
        ),
        uuid4(),
        NOW,
        route_recheck_no_grace=True,
    )
    updates = {update.field: update.value for update in plan.updates}

    assert plan.new_state == "RECHECK_DUE"
    assert updates["recheck_attempted_at"] == NOW
    assert updates["recheck_failure_reason"] is None
    assert updates["grace_expires_at"] is None
    assert updates["previous_verification_valid"] is True


def _valid_evidence(
    target_id: UUID, is_prof: bool = True, version: int = 1
) -> MagicMock:
    ev = MagicMock(spec=ProviderTrustVerificationEvidence)
    ev.id = uuid4()
    if is_prof:
        ev.professional_verification_id = target_id
        ev.facility_verification_id = None
    else:
        ev.facility_verification_id = target_id
        ev.professional_verification_id = None
    ev.origin = VerificationEvidenceOrigin.SERVER_REGISTRY_OBSERVATION
    ev.outcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE
    ev.identity_binding_result = VerificationIdentityBindingResult.MATCHED
    ev.observed_resource_version = version
    ev.source_id = "test-source-id"
    ev.adapter_version = "v1.0.0"
    return ev


def _mock_db(evidence: object | None) -> MagicMock:
    mock_db = MagicMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = evidence
    mock_db.execute = AsyncMock(return_value=mock_res)
    return mock_db


def test_human_provenance_transition_matrix_professional() -> None:
    """Proves professional transitions bind, clear, or preserve provenance per frozen matrix."""

    async def _run() -> None:
        target = ProfessionalVerification(
            id=uuid4(), provider_id=uuid4(), status="VERIFIED", version=1
        )
        ev = _valid_evidence(target.id, is_prof=True, version=1)
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev))

        # SUBMIT -> NULL (evidence disallowed)
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.SUBMIT, target, None, 1
        )
        assert target.server_provenance_evidence_id is None
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional", ProfessionalTransitionCommand.SUBMIT, target, ev.id, 1
            )
        assert exc.value.code == "INVALID_REQUEST"

        # VERIFY with valid evidence -> SET
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.VERIFY, target, ev.id, 1
        )
        assert target.server_provenance_evidence_id == ev.id

        # VERIFY without evidence -> NULL
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.VERIFY, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # MARK_RECHECK_DUE -> PRESERVE
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "professional",
            ProfessionalTransitionCommand.MARK_RECHECK_DUE,
            target,
            None,
            1,
        )
        assert target.server_provenance_evidence_id == ev.id
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.MARK_RECHECK_DUE,
                target,
                ev.id,
                1,
            )
        assert exc.value.code == "INVALID_REQUEST"

        # COMPLETE_RECHECK with valid evidence -> SET
        await service._bind_server_provenance(
            "professional",
            ProfessionalTransitionCommand.COMPLETE_RECHECK,
            target,
            ev.id,
            1,
        )
        assert target.server_provenance_evidence_id == ev.id

        # COMPLETE_RECHECK without evidence -> NULL
        await service._bind_server_provenance(
            "professional",
            ProfessionalTransitionCommand.COMPLETE_RECHECK,
            target,
            None,
            1,
        )
        assert target.server_provenance_evidence_id is None

        # SUSPEND -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.SUSPEND, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # RESTORE with evidence -> SET
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.RESTORE, target, ev.id, 1
        )
        assert target.server_provenance_evidence_id == ev.id

        # RESTORE without evidence -> NULL
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.RESTORE, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # REJECT -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.REJECT, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # MARK_STALE -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.MARK_STALE, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # REVOKE -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.REVOKE, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # EXPIRE -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "professional", ProfessionalTransitionCommand.EXPIRE, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

    asyncio.run(_run())


def test_human_provenance_transition_matrix_facility() -> None:
    """Proves facility transitions bind, clear, or preserve provenance per frozen matrix."""

    async def _run() -> None:
        target = FacilityVerification(
            id=uuid4(), facility_id=uuid4(), status="VERIFIED", version=1
        )
        ev = _valid_evidence(target.id, is_prof=False, version=1)
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev))

        # SUBMIT -> NULL (evidence disallowed)
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.SUBMIT, target, None, 1
        )
        assert target.server_provenance_evidence_id is None
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "facility", FacilityTransitionCommand.SUBMIT, target, ev.id, 1
            )
        assert exc.value.code == "INVALID_REQUEST"

        # VERIFY with evidence -> SET
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.VERIFY, target, ev.id, 1
        )
        assert target.server_provenance_evidence_id == ev.id

        # VERIFY without evidence -> NULL
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.VERIFY, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # MARK_RECHECK_REQUIRED -> PRESERVE
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.MARK_RECHECK_REQUIRED, target, None, 1
        )
        assert target.server_provenance_evidence_id == ev.id
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "facility",
                FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
                target,
                ev.id,
                1,
            )
        assert exc.value.code == "INVALID_REQUEST"

        # COMPLETE_RECHECK with evidence -> SET
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.COMPLETE_RECHECK, target, ev.id, 1
        )
        assert target.server_provenance_evidence_id == ev.id

        # COMPLETE_RECHECK without evidence -> NULL
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.COMPLETE_RECHECK, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # SUSPEND -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.SUSPEND, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # RESTORE with evidence -> SET
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.RESTORE, target, ev.id, 1
        )
        assert target.server_provenance_evidence_id == ev.id

        # RESTORE without evidence -> NULL
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.RESTORE, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # REJECT -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.REJECT, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

        # CLOSE -> CLEAR
        target.server_provenance_evidence_id = ev.id
        await service._bind_server_provenance(
            "facility", FacilityTransitionCommand.CLOSE, target, None, 1
        )
        assert target.server_provenance_evidence_id is None

    asyncio.run(_run())


def test_bind_server_provenance_validation_failures() -> None:
    """Proves invalid server evidence anchors are rejected with exact error codes."""

    async def _run() -> None:
        target = ProfessionalVerification(
            id=uuid4(), provider_id=uuid4(), status="VERIFIED", version=1
        )

        # Affiliation rejects evidence
        service_aff = ProviderTrustLifecycleApplicationService(db=_mock_db(None))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service_aff._bind_server_provenance(
                "affiliation",
                AffiliationTransitionCommand.SUSPEND,
                target,
                uuid4(),
                1,
            )
        assert exc.value.code == "INVALID_REQUEST"

        # Missing evidence in DB -> RESOURCE_NOT_FOUND
        service_none = ProviderTrustLifecycleApplicationService(db=_mock_db(None))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service_none._bind_server_provenance(
                "professional", ProfessionalTransitionCommand.VERIFY, target, uuid4(), 1
            )
        assert exc.value.code == "RESOURCE_NOT_FOUND"

        # Target ID mismatch -> EVIDENCE_BINDING_MISMATCH
        ev_wrong_id = _valid_evidence(uuid4(), is_prof=True, version=1)
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_wrong_id))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_wrong_id.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

        # Target resource kind mismatch -> EVIDENCE_BINDING_MISMATCH
        ev_facility = _valid_evidence(target.id, is_prof=False, version=1)
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_facility))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_facility.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

        # Origin mismatch -> EVIDENCE_BINDING_MISMATCH
        ev_bad_origin = _valid_evidence(target.id, is_prof=True, version=1)
        ev_bad_origin.origin = VerificationEvidenceOrigin.MANUAL_REVIEWER_ATTESTATION
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_bad_origin))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_bad_origin.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

        # Outcome mismatch -> EVIDENCE_BINDING_MISMATCH
        ev_bad_outcome = _valid_evidence(target.id, is_prof=True, version=1)
        ev_bad_outcome.outcome = VerificationEvidenceOutcome.CONFIRMED_INACTIVE
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_bad_outcome))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_bad_outcome.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

        # Identity binding mismatch -> EVIDENCE_BINDING_MISMATCH
        ev_bad_binding = _valid_evidence(target.id, is_prof=True, version=1)
        ev_bad_binding.identity_binding_result = (
            VerificationIdentityBindingResult.MISMATCHED
        )
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_bad_binding))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_bad_binding.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

        # Observed version mismatch -> EVIDENCE_BINDING_MISMATCH
        ev_bad_ver = _valid_evidence(target.id, is_prof=True, version=2)
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_bad_ver))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_bad_ver.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

        # Source ID empty -> EVIDENCE_BINDING_MISMATCH
        ev_bad_src = _valid_evidence(target.id, is_prof=True, version=1)
        ev_bad_src.source_id = "   "
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_bad_src))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_bad_src.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

        # Adapter version empty -> EVIDENCE_BINDING_MISMATCH
        ev_bad_ver_str = _valid_evidence(target.id, is_prof=True, version=1)
        ev_bad_ver_str.adapter_version = ""
        service = ProviderTrustLifecycleApplicationService(db=_mock_db(ev_bad_ver_str))
        with pytest.raises(ProviderTrustLifecycleApplicationError) as exc:
            await service._bind_server_provenance(
                "professional",
                ProfessionalTransitionCommand.VERIFY,
                target,
                ev_bad_ver_str.id,
                1,
            )
        assert exc.value.code == "EVIDENCE_BINDING_MISMATCH"

    asyncio.run(_run())


def test_dto_evidence_and_system_actor_prohibition() -> None:
    """Asserts no provider-trust route request DTO contains forbidden automation fields."""
    forbidden_fields = {
        "server_provenance_evidence_id",
        "evidence_id",
        "system_actor",
        "automation_enabled",
    }
    for mod in (ptr, ptpr):
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, BaseModel) and cls is not BaseModel:
                for field in cls.model_fields:
                    assert (
                        field not in forbidden_fields
                    ), f"Forbidden field '{field}' found on {mod.__name__}.{name}"


def test_route_surface_frozen_and_isolated() -> None:
    """Asserts exactly 26 POST provider-trust routes and AST isolation of automation service."""
    # 24 lifecycle routes + 2 permission routes = 26 provider-trust routes
    total_routes = len(ptr.router.routes) + len(ptpr.router.routes)
    assert total_routes == 26

    for r in ptr.router.routes:
        assert r.methods == {"POST"}
    for r in ptpr.router.routes:
        assert r.methods == {"POST"}

    # AST isolation check
    repo_root = Path(__file__).resolve().parents[1]
    api_files = list((repo_root / "app/api").rglob("*.py")) + [
        repo_root / "app/main.py"
    ]
    for file_path in api_files:
        content = file_path.read_text(encoding="utf-8")
        assert (
            "ProviderVerificationApplicationService" not in content
        ), f"ProviderVerificationApplicationService exposed in {file_path}"
