"""Integration tests for Squad D: Tombstone Redirects.

Contract:
- Resolve merged cards through the server-side canonical patient record.
- Return only an opaque discovery capability to the client.
- Block inactive cards.

DEFECT 5: exercises the real POST /api/v2/nfc/resolve route, backed by
the real CardResolutionService and CardRedirectService, against a fake
DB that actually understands nfc_card_registry / patient_tombstones
queries -- not a route-existence check.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_db_session, get_provider_context
from app.main import app
from app.models.nfc_card_registry import NFCCardStatus
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from tests.conftest import FakeRedis


def _provider() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(), display_name="Dr. NFC", contact_email="n@ex.com"
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(), facility_code="H", display_name="H"
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
    )


class FakeCard:
    def __init__(
        self, card_uid: str, patient_id, status: str = NFCCardStatus.ACTIVE.value
    ):
        self.card_uid = card_uid
        self.patient_id = patient_id
        self.status = status


class FakeTombstone:
    def __init__(self, old_patient_uuid, canonical_patient_uuid):
        self.old_patient_uuid = old_patient_uuid
        self.canonical_patient_uuid = canonical_patient_uuid
        self.merged_at = datetime.now(timezone.utc)


def _compiled_sql(stmt) -> str:
    try:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))
    except Exception:
        return str(stmt)


class FakeDB:
    def __init__(self, cards: list[FakeCard], tombstones: list[FakeTombstone]):
        self.cards = cards
        self.tombstones = tombstones
        self.patient_get_ids = []

    async def get(self, _model, patient_id):
        self.patient_get_ids.append(patient_id)
        return MagicMock(patient_uuid=patient_id, is_deleted=False)

    async def execute(self, stmt):
        sql = _compiled_sql(stmt)
        result = MagicMock()
        if "nfc_card_registry" in sql:
            match = next((c for c in self.cards if c.card_uid in sql), None)
            result.scalar_one_or_none.return_value = match
            return result
        if "patient_tombstones" in sql:
            matches = [t for t in self.tombstones if t.old_patient_uuid.hex in sql]
            result.scalars.return_value.all.return_value = matches
            return result
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result


def _client_with_overrides(db: FakeDB):
    provider = _provider()
    app.dependency_overrides[get_provider_context] = lambda: provider
    app.dependency_overrides[get_db_session] = lambda: db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _bypass_rate_limit():
    fake_redis = FakeRedis()

    with (
        patch("app.api.v2.nfc_routes.get_async_redis_client", return_value=fake_redis),
        patch(
            "app.api.v2.nfc_routes.atomic_fixed_window", AsyncMock(return_value=(1, 60))
        ),
        patch(
            "app.api.v2.nfc_routes.append_audit_log_or_503",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.card_resolution_service.append_audit_log_or_503",
            AsyncMock(return_value=None),
        ),
    ):
        yield


def test_merged_card_redirect():
    """A merged card resolves server-side without disclosing identity data."""
    old_patient = uuid.uuid4()
    canonical_patient = uuid.uuid4()
    db = FakeDB(
        cards=[FakeCard("OLD-CARD-UID", old_patient)],
        tombstones=[FakeTombstone(old_patient, canonical_patient)],
    )
    client = _client_with_overrides(db)

    resp = client.post("/api/v2/nfc/resolve", json={"card_uid": "OLD-CARD-UID"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == {"discovery_handle", "expires_at"}
    assert data["discovery_handle"]
    assert data["expires_at"]
    for field in ("patient_id", "canonical_patient_id", "is_redirected"):
        assert field not in data
    assert db.patient_get_ids == [canonical_patient]


def test_inactive_card_rejected():
    db = FakeDB(
        cards=[
            FakeCard(
                "INACTIVE-CARD-UID", uuid.uuid4(), status=NFCCardStatus.REVOKED.value
            )
        ],
        tombstones=[],
    )
    client = _client_with_overrides(db)

    resp = client.post("/api/v2/nfc/resolve", json={"card_uid": "INACTIVE-CARD-UID"})
    assert resp.status_code == 403


def test_non_redirected_card_resolves_normally():
    """An active non-merged card also returns only opaque discovery output."""
    patient_id = uuid.uuid4()
    db = FakeDB(cards=[FakeCard("NORMAL-CARD-UID", patient_id)], tombstones=[])
    client = _client_with_overrides(db)

    resp = client.post("/api/v2/nfc/resolve", json={"card_uid": "NORMAL-CARD-UID"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == {"discovery_handle", "expires_at"}
    assert data["discovery_handle"]


def test_bounded_multi_hop_redirect_chain():
    """A bounded merge chain resolves server-side and remains opaque externally."""
    patient_a, patient_b, patient_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = FakeDB(
        cards=[FakeCard("CHAIN-CARD-UID", patient_a)],
        tombstones=[
            FakeTombstone(patient_a, patient_b),
            FakeTombstone(patient_b, patient_c),
        ],
    )
    client = _client_with_overrides(db)

    resp = client.post("/api/v2/nfc/resolve", json={"card_uid": "CHAIN-CARD-UID"})
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {"discovery_handle", "expires_at"}
    assert db.patient_get_ids == [patient_c]


def test_cycle_detected_fails_closed_with_security_unavailable():
    """A -> B -> A is a data-integrity violation, not an infinite redirect
    -- must be detected and rejected, never looped forever."""
    patient_a, patient_b = uuid.uuid4(), uuid.uuid4()
    db = FakeDB(
        cards=[FakeCard("CYCLE-CARD-UID", patient_a)],
        tombstones=[
            FakeTombstone(patient_a, patient_b),
            FakeTombstone(patient_b, patient_a),
        ],
    )
    client = _client_with_overrides(db)

    resp = client.post("/api/v2/nfc/resolve", json={"card_uid": "CYCLE-CARD-UID"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "NFC_SECURITY_CONTROL_UNAVAILABLE"


def test_duplicate_tombstones_fail_closed_with_security_unavailable():
    """Two tombstones both claiming the same old_patient_uuid is an
    ambiguous, invalid state -- must fail closed, not pick one silently."""
    old_patient = uuid.uuid4()
    db = FakeDB(
        cards=[FakeCard("DUP-CARD-UID", old_patient)],
        tombstones=[
            FakeTombstone(old_patient, uuid.uuid4()),
            FakeTombstone(old_patient, uuid.uuid4()),
        ],
    )
    client = _client_with_overrides(db)

    resp = client.post("/api/v2/nfc/resolve", json={"card_uid": "DUP-CARD-UID"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "NFC_SECURITY_CONTROL_UNAVAILABLE"


def test_unknown_card_rejected():
    db = FakeDB(cards=[], tombstones=[])
    client = _client_with_overrides(db)

    resp = client.post("/api/v2/nfc/resolve", json={"card_uid": "NEVER-REGISTERED"})
    assert resp.status_code == 403
