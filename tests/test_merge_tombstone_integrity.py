"""Regression tests for patient merge and tombstone redirect integrity."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.patient_tombstone import PatientTombstone
from app.services.card_redirect_service import (
    CardRedirectService,
    TombstoneIntegrityError,
)
from app.services.merge_service import PatientMergeService


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeResult:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def scalars(self):
        return FakeScalars(self._rows)

    def scalar_one_or_none(self):
        return self._one


class FakeMergeDB:
    def __init__(self):
        self.patients = {}
        self.tombstones = []
        self.cards = {}
        self.added = []
        self.committed = False

    async def get(self, _model, key):
        return self.patients.get(key)

    async def execute(self, stmt):
        sql = str(stmt).lower()
        params = stmt.compile().params
        if "patient_tombstones" in sql:
            patient_uuid = next(
                value for key, value in params.items() if "old_patient_uuid" in key
            )
            rows = [
                row for row in self.tombstones if row.old_patient_uuid == patient_uuid
            ]
            return FakeResult(rows=rows)
        if "nfc_card_registry" in sql:
            card_id = next(value for key, value in params.items() if "card_uid" in key)
            return FakeResult(one=self.cards.get(card_id))
        return FakeResult()

    def add(self, row):
        self.added.append(row)
        if isinstance(row, PatientTombstone):
            self.tombstones.append(row)

    async def commit(self):
        self.committed = True

    async def refresh(self, _row):
        return None


def patient(patient_uuid):
    return SimpleNamespace(patient_uuid=patient_uuid, is_deleted=False, updated_at=None)


def tombstone(old_uuid, canonical_uuid):
    return PatientTombstone(
        old_patient_uuid=old_uuid,
        canonical_patient_uuid=canonical_uuid,
        merged_at=datetime.now(timezone.utc),
        merged_by="tester",
        reason="test",
    )


def merge_db(*patient_ids):
    db = FakeMergeDB()
    for pid in patient_ids:
        db.patients[pid] = patient(pid)
    return db


@pytest.mark.asyncio
async def test_self_merge_rejected():
    old = uuid.uuid4()
    db = merge_db(old)

    with pytest.raises(ValueError, match="itself"):
        await PatientMergeService(db).merge_patients(
            old_uuid=old, canonical_uuid=old, reason="bad"
        )


@pytest.mark.asyncio
async def test_duplicate_merge_rejected_when_old_has_different_tombstone():
    old, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = merge_db(old, first, second)
    db.tombstones.append(tombstone(old, first))

    with pytest.raises(ValueError, match="already been merged"):
        await PatientMergeService(db).merge_patients(
            old_uuid=old, canonical_uuid=second, reason="bad"
        )


@pytest.mark.asyncio
async def test_duplicate_tombstone_rows_rejected():
    old, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = merge_db(old, first, second)
    db.tombstones.extend([tombstone(old, first), tombstone(old, second)])

    with pytest.raises(ValueError, match="Duplicate tombstones"):
        await PatientMergeService(db).merge_patients(
            old_uuid=old, canonical_uuid=first, reason="bad"
        )


@pytest.mark.asyncio
async def test_cycle_merge_rejected():
    a, b = uuid.uuid4(), uuid.uuid4()
    db = merge_db(a, b)
    db.tombstones.append(tombstone(a, b))

    with pytest.raises(ValueError, match="cycle"):
        await PatientMergeService(db).merge_patients(
            old_uuid=b, canonical_uuid=a, reason="bad"
        )


@pytest.mark.asyncio
async def test_chain_merge_and_card_redirect_resolve_to_final_canonical():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = merge_db(a, b, c)
    db.tombstones.append(tombstone(a, b))
    db.cards["CARD-A"] = SimpleNamespace(
        card_uid="CARD-A", status="ACTIVE", patient_id=a
    )

    created = await PatientMergeService(db).merge_patients(
        old_uuid=b, canonical_uuid=c, reason="chain"
    )
    assert created.canonical_patient_uuid == c

    result = await CardRedirectService(db).resolve_card_with_redirect("CARD-A")
    assert result["canonical_patient_uuid"] == str(c)
    assert result["is_redirected"] is True
    assert [hop["from"] for hop in result["redirect_chain"]] == [str(a), str(b)]


@pytest.mark.asyncio
async def test_card_redirect_duplicate_tombstones_raise_integrity_error():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = merge_db(a, b, c)
    db.cards["CARD-A"] = SimpleNamespace(
        card_uid="CARD-A", status="ACTIVE", patient_id=a
    )
    db.tombstones.extend([tombstone(a, b), tombstone(a, c)])

    with pytest.raises(TombstoneIntegrityError, match="Duplicate tombstones"):
        await CardRedirectService(db).resolve_card_with_redirect("CARD-A")


@pytest.mark.asyncio
async def test_card_redirect_cycle_fails_closed():
    a, b = uuid.uuid4(), uuid.uuid4()
    db = merge_db(a, b)
    db.cards["CARD-A"] = SimpleNamespace(
        card_uid="CARD-A", status="ACTIVE", patient_id=a
    )
    db.tombstones.extend([tombstone(a, b), tombstone(b, a)])

    with pytest.raises(TombstoneIntegrityError, match="cycle"):
        await CardRedirectService(db).resolve_card_with_redirect("CARD-A")
