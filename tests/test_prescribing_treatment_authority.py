from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.security.provider_capabilities import ClinicalCapability
from app.services import treatment_session_v1_authority as authority
from app.services.clinical_eligibility import ClinicalAuthenticationMethod
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
)


NOW = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)


def _request_data(*, prescription: bool) -> dict:
    operations = ["CREATE_ENCOUNTER", "WRITE_VITALS"]
    if prescription:
        operations.append("WRITE_PRESCRIPTION")
    return {
        "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        "provider_id": str(uuid4()),
        "hospital_id": str(uuid4()),
        "request_id": str(uuid4()),
        "clinical_authentication_method": (
            ClinicalAuthenticationMethod.PROVIDER_SESSION.value
        ),
        "clinical_initiated_at": (NOW - timedelta(seconds=10)).isoformat(),
        "clinical_mfa_verified_at": (NOW - timedelta(seconds=11)).isoformat(),
        "clinical_assurance_policy_version": "clinical-contact-email-and-phone/v1",
        "allowed_operations": operations,
        "status": "approved",
    }


@pytest.mark.asyncio
async def test_live_treatment_authority_adds_prescribing_revalidation(monkeypatch):
    seen: list[ClinicalCapability] = []

    async def evaluate(_self, _db, _provider, _hospital, assurance, capability):
        assert assurance.required_capability is capability
        seen.append(capability)
        return SimpleNamespace(allowed=True, denial_code=None)

    monkeypatch.setattr(
        authority.ClinicalEligibilityService,
        "evaluate_delegated",
        evaluate,
    )

    await authority.assert_live_treatment_session_v1_provider(
        db=SimpleNamespace(),
        request_data=_request_data(prescription=True),
    )
    assert seen == [
        ClinicalCapability.CONSENT_REQUEST,
        ClinicalCapability.PRESCRIBE_MEDICATION,
    ]


@pytest.mark.asyncio
async def test_non_prescription_treatment_keeps_existing_authority_shape(monkeypatch):
    seen: list[ClinicalCapability] = []

    async def evaluate(_self, _db, _provider, _hospital, assurance, capability):
        seen.append(capability)
        return SimpleNamespace(allowed=True, denial_code=None)

    monkeypatch.setattr(
        authority.ClinicalEligibilityService,
        "evaluate_delegated",
        evaluate,
    )

    await authority.assert_live_treatment_session_v1_provider(
        db=SimpleNamespace(),
        request_data=_request_data(prescription=False),
    )
    assert seen == [ClinicalCapability.CONSENT_REQUEST]


def test_request_and_mutation_boundaries_are_operation_sensitive() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    request_source = (
        root / "app" / "api" / "v2" / "treatment_session_v1_routes.py"
    ).read_text(encoding="utf-8")
    gate_source = (root / "app" / "core" / "clinical_session_gate.py").read_text(
        encoding="utf-8"
    )

    assert "ClinicalAccessOperation.WRITE_PRESCRIPTION.value in operations" in request_source
    assert "ClinicalCapability.PRESCRIBE_MEDICATION" in request_source
    assert "enforce_current_clinical_capability" in request_source

    assert "operation is ClinicalAccessOperation.WRITE_PRESCRIPTION" in gate_source
    assert "ClinicalCapability.PRESCRIBE_MEDICATION" in gate_source
    assert "assert_current_prescribing_eligibility" in gate_source
    assert "lock_professional=True" in gate_source

def test_patient_denial_does_not_require_current_prescriber_authority() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (
        root / "app" / "api" / "v2" / "treatment_session_v1_routes.py"
    ).read_text(encoding="utf-8")

    approval_guard = 'if payload.decision == "approved":'
    authority_call = (
        "await assert_live_treatment_session_v1_provider(db=db, request_data=data)"
    )
    guard_index = source.index(approval_guard)
    call_index = source.index(authority_call, guard_index)
    verifier_index = source.index(
        "result = await SignedTreatmentSessionV1Verifier().verify(", call_index
    )

    assert guard_index < call_index < verifier_index
    assert "patient denial creates no provider authority" in source

