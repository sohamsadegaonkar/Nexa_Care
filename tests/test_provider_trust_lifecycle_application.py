from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.provider import ProviderHospitalAffiliation
from app.services.provider_trust_lifecycle import (
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
)
from app.services.provider_trust_lifecycle_application import (
    ProviderTrustLifecycleApplicationError,
    ProviderTrustLifecycleApplicationService,
    _request_hash,
)


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
