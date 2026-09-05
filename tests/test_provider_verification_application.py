"""Unit and isolation qualification for ProviderVerificationApplicationService (Phase 5E).

Verifies invocation, envelope, source policy, system actor, route freeze, and
fail-closed behaviors in complete isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.main import app
from app.models.provider import (
    VerificationEvidenceLookupPurpose,
    VerificationEvidenceOutcome,
    VerificationIdentityBindingResult,
)
from app.services.provider_verification_application import (
    SYSTEM_AUTOMATION_ACTOR_ID,
    RegistryLookupInvocation,
    SourceAutomationPolicy,
    SourceAutomationPolicyRegistry,
    SystemAutomationActor,
    ValidatedRegistryLookupEnvelope,
    VerificationApplicationError,
    _canonical_envelope_hash,
    execute_lookup_and_create_envelope,
)
from app.services.provider_verification_registry import (
    ProfessionalLookupRequest,
    RegistryObservation,
    RegistryResourceType,
)


def _make_prof_obs(
    *,
    outcome: VerificationEvidenceOutcome = VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
    purpose: VerificationEvidenceLookupPurpose = VerificationEvidenceLookupPurpose.RECHECK,
    source_id: str = "SRC_PROF_01",
    observed_at: datetime | None = None,
) -> RegistryObservation:
    return RegistryObservation(
        resource_type=RegistryResourceType.PROFESSIONAL,
        source_id=source_id,
        adapter_version="1.0.0",
        observed_at=observed_at or (datetime.now(timezone.utc) - timedelta(minutes=5)),
        lookup_purpose=purpose,
        outcome=outcome,
        identity_binding_result=VerificationIdentityBindingResult.MATCHED,
        binding_method="REGISTRY_MATCH",
    )


def test_system_automation_actor_identity() -> None:
    actor = SystemAutomationActor()
    assert actor.actor_id == "system:registry_verification_automation"
    assert actor.actor_id == SYSTEM_AUTOMATION_ACTOR_ID
    assert actor.actor_type == "SYSTEM_AUTOMATION"
    assert actor.execution_mode == "SYSTEM_AUTOMATION"


def test_ast_import_guard_no_service_import_in_api() -> None:
    """Verify app/api/** and app/main.py do not import or expose ProviderVerificationApplicationService."""
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    paths_to_check = list((repo_root / "app" / "api").rglob("*.py")) + [
        repo_root / "app" / "main.py"
    ]
    for path in paths_to_check:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert (
                        "ProviderVerificationApplicationService" not in alias.name
                    ), f"{path} imports ProviderVerificationApplicationService"
            elif isinstance(node, ast.ImportFrom):
                if node.module and "provider_verification_application" in node.module:
                    for alias in node.names:
                        assert (
                            alias.name != "ProviderVerificationApplicationService"
                        ), f"{path} imports ProviderVerificationApplicationService"


def test_source_automation_policy_matrix_matching() -> None:
    registry = SourceAutomationPolicyRegistry()
    registry.register(
        SourceAutomationPolicy(
            source_id="MAHA_REG_01",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="MAHA_MED",
            approved_adapter_version="1.2.0",
            allowed_binding_methods=frozenset({"REGISTRY_MATCH"}),
            automation_enabled=True,
            recheck_interval_seconds=86400 * 30,
        )
    )

    # Match exact
    p = registry.get_policy(
        "MAHA_REG_01",
        resource_type=RegistryResourceType.PROFESSIONAL,
        registration_authority_code="MAHA_MED",
    )
    assert p.automation_enabled is True
    assert p.approved_adapter_version == "1.2.0"

    # Mismatch resource type
    p_fac = registry.get_policy(
        "MAHA_REG_01",
        resource_type=RegistryResourceType.FACILITY,
        registration_authority_code="MAHA_MED",
    )
    assert p_fac.automation_enabled is False

    # Mismatch authority code
    p_auth = registry.get_policy(
        "MAHA_REG_01",
        resource_type=RegistryResourceType.PROFESSIONAL,
        registration_authority_code="OTHER_COUNCIL",
    )
    assert p_auth.automation_enabled is False


def test_kill_switch_injected_configuration() -> None:
    from unittest.mock import MagicMock
    from app.services.provider_verification_application import (
        ProviderVerificationApplicationService,
    )

    # Default False
    svc_default = ProviderVerificationApplicationService(db=MagicMock())
    assert svc_default._is_global_automation_enabled() is False

    # Injected boolean
    svc_bool = ProviderVerificationApplicationService(
        db=MagicMock(), automation_enabled=True
    )
    assert svc_bool._is_global_automation_enabled() is True

    # Injected callable
    flag = False
    svc_callable = ProviderVerificationApplicationService(
        db=MagicMock(), automation_enabled=lambda: flag
    )
    assert svc_callable._is_global_automation_enabled() is False
    flag = True
    assert svc_callable._is_global_automation_enabled() is True


def test_registry_lookup_invocation_validation() -> None:
    req = ProfessionalLookupRequest(
        registration_authority_code="MED_COUNCIL",
        registration_number_normalized="DOC12345",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    valid_id = uuid4()
    inv = RegistryLookupInvocation(
        resource_id=valid_id,
        resource_type=RegistryResourceType.PROFESSIONAL,
        expected_version=1,
        request=req,
    )
    assert inv.resource_id == valid_id
    assert inv.expected_version == 1
    assert inv.invoked_at.tzinfo is not None

    with pytest.raises(VerificationApplicationError):
        RegistryLookupInvocation(
            resource_id="not-a-uuid",  # type: ignore[arg-type]
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
        )

    with pytest.raises(VerificationApplicationError):
        RegistryLookupInvocation(
            resource_id=valid_id,
            resource_type="PROFESSIONAL",  # type: ignore[arg-type]
            expected_version=1,
            request=req,
        )

    with pytest.raises(VerificationApplicationError):
        RegistryLookupInvocation(
            resource_id=valid_id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=0,
            request=req,
        )

    with pytest.raises(VerificationApplicationError):
        RegistryLookupInvocation(
            resource_id=valid_id,
            resource_type=RegistryResourceType.PROFESSIONAL,
            expected_version=1,
            request=req,
            invoked_at=datetime(2026, 9, 5, 12, 0, 0),  # naive
        )


def test_validated_envelope_structural_integrity() -> None:
    req = ProfessionalLookupRequest(
        registration_authority_code="MED_COUNCIL",
        registration_number_normalized="DOC12345",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    inv_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    inv = RegistryLookupInvocation(
        resource_id=uuid4(),
        resource_type=RegistryResourceType.PROFESSIONAL,
        expected_version=1,
        request=req,
        invoked_at=inv_time,
    )

    # Valid matching observation
    obs = _make_prof_obs(observed_at=inv_time + timedelta(seconds=2))
    envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)
    assert envelope.invocation == inv
    assert envelope.observation == obs

    # Purpose mismatch
    obs_diff_purpose = _make_prof_obs(
        purpose=VerificationEvidenceLookupPurpose.INITIAL_VERIFICATION,
        observed_at=inv_time + timedelta(seconds=2),
    )
    with pytest.raises(VerificationApplicationError, match="ENVELOPE_PURPOSE_MISMATCH"):
        ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs_diff_purpose)

    # Resource type mismatch
    obs_fac = RegistryObservation(
        resource_type=RegistryResourceType.FACILITY,
        source_id="SRC_FAC_01",
        adapter_version="1.0.0",
        observed_at=inv_time + timedelta(seconds=2),
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
        outcome=VerificationEvidenceOutcome.CONFIRMED_ACTIVE,
        identity_binding_result=VerificationIdentityBindingResult.MATCHED,
    )
    with pytest.raises(
        VerificationApplicationError, match="ENVELOPE_RESOURCE_TYPE_MISMATCH"
    ):
        ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs_fac)

    # Observed before invocation
    obs_before = _make_prof_obs(observed_at=inv_time - timedelta(seconds=2))
    with pytest.raises(
        VerificationApplicationError, match="ENVELOPE_OBSERVED_BEFORE_INVOCATION"
    ):
        ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs_before)


def test_source_automation_policy_default_deny() -> None:
    registry = SourceAutomationPolicyRegistry()
    policy = registry.get_policy("UNKNOWN_SOURCE")
    assert policy.source_id == "UNKNOWN_SOURCE"
    assert policy.automation_enabled is False

    # Validation errors when automation_enabled=True without required fields
    with pytest.raises(ValueError, match="resource_type"):
        SourceAutomationPolicy(
            source_id="SRC_PROF_01",
            automation_enabled=True,
        )

    with pytest.raises(ValueError, match="registration_authority_code"):
        SourceAutomationPolicy(
            source_id="SRC_PROF_01",
            resource_type=RegistryResourceType.PROFESSIONAL,
            automation_enabled=True,
        )

    with pytest.raises(ValueError, match="approved_adapter_version"):
        SourceAutomationPolicy(
            source_id="SRC_PROF_01",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="MED_COUNCIL",
            automation_enabled=True,
        )

    with pytest.raises(ValueError, match="allowed_binding_methods"):
        SourceAutomationPolicy(
            source_id="SRC_PROF_01",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="MED_COUNCIL",
            approved_adapter_version="1.0.0",
            allowed_binding_methods=frozenset(),
            automation_enabled=True,
            recheck_interval_seconds=86400 * 30,
        )

    with pytest.raises(ValueError, match="recheck_interval_seconds"):
        SourceAutomationPolicy(
            source_id="SRC_PROF_01",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="MED_COUNCIL",
            approved_adapter_version="1.0.0",
            allowed_binding_methods=frozenset({"REGISTRY_MATCH"}),
            automation_enabled=True,
            recheck_interval_seconds=None,
        )

    with pytest.raises(ValueError, match="recheck_interval_seconds"):
        SourceAutomationPolicy(
            source_id="SRC_PROF_01",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="MED_COUNCIL",
            approved_adapter_version="1.0.0",
            allowed_binding_methods=frozenset({"REGISTRY_MATCH"}),
            automation_enabled=True,
            recheck_interval_seconds=0,
        )

    # Custom registration with all required fields
    registry.register(
        SourceAutomationPolicy(
            source_id="SRC_PROF_01",
            resource_type=RegistryResourceType.PROFESSIONAL,
            registration_authority_code="MED_COUNCIL",
            approved_adapter_version="1.0.0",
            automation_enabled=True,
            recheck_interval_seconds=86400 * 30,
            allowed_binding_methods=frozenset({"REGISTRY_MATCH", "TOKEN_MATCH"}),
        )
    )
    p = registry.get_policy(
        "SRC_PROF_01",
        resource_type=RegistryResourceType.PROFESSIONAL,
        registration_authority_code="MED_COUNCIL",
    )
    assert p.automation_enabled is True
    assert "TOKEN_MATCH" in p.allowed_binding_methods


def test_canonical_envelope_hash_determinism() -> None:
    req = ProfessionalLookupRequest(
        registration_authority_code="MED_COUNCIL",
        registration_number_normalized="DOC12345",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    inv_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    res_id = uuid4()
    inv = RegistryLookupInvocation(
        resource_id=res_id,
        resource_type=RegistryResourceType.PROFESSIONAL,
        expected_version=2,
        request=req,
        invoked_at=inv_time,
    )
    obs = _make_prof_obs(observed_at=inv_time)
    envelope = ValidatedRegistryLookupEnvelope(invocation=inv, observation=obs)

    h1 = _canonical_envelope_hash(envelope)
    h2 = _canonical_envelope_hash(envelope)
    assert h1 == h2
    assert len(h1) == 64


@pytest.mark.asyncio
async def test_execute_lookup_and_create_envelope_helper() -> None:
    req = ProfessionalLookupRequest(
        registration_authority_code="MED_COUNCIL",
        registration_number_normalized="DOC12345",
        lookup_purpose=VerificationEvidenceLookupPurpose.RECHECK,
    )
    inv_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    inv = RegistryLookupInvocation(
        resource_id=uuid4(),
        resource_type=RegistryResourceType.PROFESSIONAL,
        expected_version=1,
        request=req,
        invoked_at=inv_time,
    )
    mock_adapter = AsyncMock()
    mock_adapter.lookup_professional.return_value = _make_prof_obs(
        observed_at=inv_time + timedelta(seconds=1)
    )

    envelope = await execute_lookup_and_create_envelope(mock_adapter, inv)
    assert isinstance(envelope, ValidatedRegistryLookupEnvelope)
    assert envelope.observation.source_id == "SRC_PROF_01"
    mock_adapter.lookup_professional.assert_awaited_once_with(req)


def test_route_freeze_exactly_26_provider_trust_post_routes() -> None:
    """Verify route freeze: zero HTTP routes added, exactly 26 POST routes under /api/v2/provider-trust."""
    routes = [route for route in app.routes if hasattr(route, "path")]
    trust_routes = [r for r in routes if "/provider-trust" in r.path]
    assert (
        len(trust_routes) == 26
    ), f"Expected exactly 26 provider-trust routes, found {len(trust_routes)}"

    for r in trust_routes:
        assert (
            "POST" in r.methods
        ), f"Route {r.path} must only accept POST, found {r.methods}"
