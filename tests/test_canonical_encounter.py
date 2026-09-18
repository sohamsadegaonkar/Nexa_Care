from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.api.v2 import treatment_session_v1_claim_routes as routes
from app.core.clinical_session_gate import (
    TreatmentSessionV1Authority,
    TreatmentSessionV1GateDenied,
    TreatmentSessionV1GateUnavailable,
)
from app.models.clinical_encounter import ClinicalEncounter
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.services import canonical_encounter as service


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _DB:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.added = []
        self.flushed = 0

    async def execute(self, _statement):
        if not self.rows:
            raise AssertionError("unexpected database query")
        return _Result(self.rows.pop(0))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed += 1


def _authority(
    operation: ClinicalAccessOperation = ClinicalAccessOperation.CREATE_ENCOUNTER,
) -> TreatmentSessionV1Authority:
    now = datetime.now(timezone.utc)
    return TreatmentSessionV1Authority(
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        purpose="treatment",
        allowed_operations=(ClinicalAccessOperation.CREATE_ENCOUNTER.value,),
        required_operation=operation,
        provider_session_binding_hash="a" * 64,
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        token_hash="b" * 64,
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=10),
        encounter_id=None,
    )


@pytest.mark.asyncio
async def test_canonical_encounter_uses_reserved_server_identifier(monkeypatch):
    authority = _authority()
    encounter_id = uuid.uuid4()
    reserve = AsyncMock(return_value=encounter_id)
    monkeypatch.setattr(service, "stage_server_encounter_binding", reserve)
    db = _DB(None)

    encounter, created = await service.stage_canonical_encounter(
        db=db,
        authority=authority,
    )

    assert created is True
    assert encounter.encounter_id == encounter_id
    assert encounter.clinical_session_id == authority.session_id
    assert encounter.patient_id == authority.patient_id
    assert encounter.provider_id == authority.provider_id
    assert encounter.hospital_id == authority.hospital_id
    assert db.added == [encounter]
    assert db.flushed == 1
    reserve.assert_awaited_once_with(db=db, authority=authority)


@pytest.mark.asyncio
async def test_canonical_encounter_is_idempotent_for_same_session(monkeypatch):
    authority = _authority()
    encounter_id = uuid.uuid4()
    existing = ClinicalEncounter(
        encounter_id=encounter_id,
        clinical_session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
    )
    monkeypatch.setattr(
        service,
        "stage_server_encounter_binding",
        AsyncMock(return_value=encounter_id),
    )
    db = _DB(existing)

    encounter, created = await service.stage_canonical_encounter(
        db=db,
        authority=authority,
    )

    assert encounter is existing
    assert created is False
    assert db.added == []
    assert db.flushed == 0


@pytest.mark.asyncio
async def test_canonical_encounter_fails_closed_on_binding_mismatch(monkeypatch):
    authority = _authority()
    encounter_id = uuid.uuid4()
    existing = ClinicalEncounter(
        encounter_id=uuid.uuid4(),
        clinical_session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
    )
    monkeypatch.setattr(
        service,
        "stage_server_encounter_binding",
        AsyncMock(return_value=encounter_id),
    )

    with pytest.raises(TreatmentSessionV1GateUnavailable) as caught:
        await service.stage_canonical_encounter(
            db=_DB(existing),
            authority=authority,
        )

    assert caught.value.code == "TREATMENT_ENCOUNTER_INTEGRITY_FAILURE"


@pytest.mark.asyncio
async def test_canonical_encounter_requires_create_encounter_operation(monkeypatch):
    authority = _authority(ClinicalAccessOperation.READ_CLINICAL_HISTORY)
    reserve = AsyncMock()
    monkeypatch.setattr(service, "stage_server_encounter_binding", reserve)

    with pytest.raises(TreatmentSessionV1GateDenied) as caught:
        await service.stage_canonical_encounter(db=_DB(), authority=authority)

    assert caught.value.code == "TREATMENT_ENCOUNTER_OPERATION_REQUIRED"
    reserve.assert_not_awaited()


def test_encounter_route_has_no_client_selected_authority_parameters():
    parameters = inspect.signature(routes.create_treatment_encounter).parameters
    assert set(parameters) == {"response", "authority", "db"}

    source = Path("app/api/v2/treatment_session_v1_claim_routes.py").read_text(
        encoding="utf-8"
    )
    assert "require_clinical_session(ClinicalAccessOperation.CREATE_ENCOUNTER)" in source
    route_block = source[source.index('@router.post(\n    "/encounter"') :]
    for forbidden in (
        "patient_id:",
        "provider_id:",
        "hospital_id:",
        "encounter_id:",
    ):
        assert forbidden not in route_block.split("return TreatmentEncounterResponse", 1)[0]


def test_legacy_clinical_write_routes_remain_unwired_to_treatment_token():
    source = Path("app/api/v2/patient_record_routes.py").read_text(encoding="utf-8")
    assert "require_clinical_session" not in source
    assert "X-Treatment-Token" not in source
