from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, False_, True_

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import (
    Allergy,
    DocumentReference,
    LabResult,
    Medication,
    PatientRecord,
    TimelineEvent,
    Vitals,
)
from scripts import link_patient_auth_identity as identity_script
from scripts import seed_demo_patient as seeder


def _expected_value(expression):
    if isinstance(expression, False_):
        return False
    if isinstance(expression, True_):
        return True
    return getattr(expression, "value", None)


def _matches(obj, expression) -> bool:
    if isinstance(expression, BooleanClauseList):
        return all(_matches(obj, child) for child in expression.get_children())
    if isinstance(expression, BinaryExpression):
        field = getattr(expression.left, "key", None)
        if field is None:
            return True
        return getattr(obj, field) == _expected_value(expression.right)
    return all(_matches(obj, child) for child in expression.get_children())


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class MemorySession:
    """Small transaction-aware ORM fake for deterministic seeder tests."""

    def __init__(self, objects=()):
        self.objects = list(objects)
        self._committed = list(objects)
        self.commit_calls = 0
        self.rollback_calls = 0

    def add(self, obj):
        self.objects.append(obj)

    async def get(self, model, primary_key):
        key = "patient_uuid" if model is Patient else "id"
        return next(
            (obj for obj in self.objects if isinstance(obj, model) and getattr(obj, key, None) == primary_key),
            None,
        )

    def _rows(self, statement):
        model = statement.column_descriptions[0]["entity"]
        rows = [obj for obj in self.objects if isinstance(obj, model)]
        if statement.whereclause is not None:
            rows = [obj for obj in rows if _matches(obj, statement.whereclause)]
        return rows

    async def scalar(self, statement):
        rows = self._rows(statement)
        return rows[0] if rows else None

    async def scalars(self, statement):
        return _ScalarRows(self._rows(statement))

    async def flush(self):
        return None

    async def commit(self):
        self.commit_calls += 1
        self._committed = list(self.objects)

    async def rollback(self):
        self.rollback_calls += 1
        self.objects = list(self._committed)


@pytest.mark.asyncio
async def test_seed_demo_patient_creates_authoritative_patient_and_clinical_records():
    session = MemorySession()

    status = await seeder.seed_aarav_sharma(session)

    assert status == "created"
    patients = [obj for obj in session.objects if isinstance(obj, Patient)]
    assert len(patients) == 1
    patient = patients[0]
    assert patient.patient_uuid == uuid.UUID("123e4567-e89b-12d3-a456-426614174001")
    assert patient.is_deleted is False
    assert patient.dek_id is None

    assert len([obj for obj in session.objects if isinstance(obj, PatientRecord)]) == 1
    assert len([obj for obj in session.objects if isinstance(obj, Vitals)]) == 3
    assert len([obj for obj in session.objects if isinstance(obj, Medication)]) == 1
    assert len([obj for obj in session.objects if isinstance(obj, Allergy)]) == 1
    assert len([obj for obj in session.objects if isinstance(obj, LabResult)]) == 1
    assert len([obj for obj in session.objects if isinstance(obj, TimelineEvent)]) == 6
    assert len([obj for obj in session.objects if isinstance(obj, DocumentReference)]) == 1


@pytest.mark.asyncio
async def test_seed_demo_patient_is_idempotent():
    session = MemorySession()
    assert await seeder.seed_aarav_sharma(session) == "created"
    first_count = len(session.objects)

    status = await seeder.seed_aarav_sharma(session)

    assert status == "already-exists"
    assert len(session.objects) == first_count


@pytest.mark.asyncio
async def test_seed_run_prints_created_then_already_exists(monkeypatch, capsys):
    session = MemorySession()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Engine:
        dispose = AsyncMock()

    monkeypatch.setattr(seeder, "get_session_factory", lambda: SessionContext)
    monkeypatch.setattr(seeder, "get_async_engine", lambda: Engine())

    assert await seeder._run() == 0
    assert capsys.readouterr().out.endswith(f"status=created patient_id={seeder.DEMO_PATIENT_ID}\n")

    assert await seeder._run() == 0
    assert capsys.readouterr().out.endswith(f"status=already-exists patient_id={seeder.DEMO_PATIENT_ID}\n")


@pytest.mark.asyncio
async def test_seed_demo_patient_rejects_soft_deleted_patient():
    patient = Patient(patient_uuid=seeder.DEMO_PATIENT_UUID, is_deleted=True)
    session = MemorySession([patient])

    with pytest.raises(seeder.DemoPatientConflict, match="soft-deleted"):
        await seeder.seed_aarav_sharma(session)

    assert session.objects == [patient]


@pytest.mark.asyncio
async def test_seed_demo_patient_rejects_conflicting_patient_data():
    source = open("scripts/seed_demo_patient.py", encoding="utf-8").read()
    assert "full_name=" not in source
    assert "phone=" not in source
    assert "email=" not in source
    assert "address_line" not in source
    assert "emergency_contact" not in source


@pytest.mark.asyncio
async def test_identity_link_accepts_seeded_authoritative_patient(monkeypatch):
    session = MemorySession()
    await seeder.seed_aarav_sharma(session)
    monkeypatch.setattr(identity_script, "append_audit_log", AsyncMock(return_value=True))

    result = await identity_script.link_patient_auth_identity(
        session,
        patient_id=seeder.DEMO_PATIENT_UUID,
        supabase_user_id="00000000-0000-4000-8000-000000000001",
    )

    assert result == "linked"
    mappings = [obj for obj in session.objects if isinstance(obj, PatientAuthIdentity)]
    assert len(mappings) == 1
    assert mappings[0].patient_id == seeder.DEMO_PATIENT_UUID


@pytest.mark.asyncio
async def test_seed_run_rolls_back_all_rows_on_failure(monkeypatch):
    session = MemorySession()

    async def fail_after_patient(active_session):
        await seeder._ensure_authoritative_patient(active_session)
        raise RuntimeError("simulated clinical seed failure")

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Engine:
        dispose = AsyncMock()

    monkeypatch.setattr(seeder, "seed_aarav_sharma", fail_after_patient)
    monkeypatch.setattr(seeder, "get_session_factory", lambda: SessionContext)
    monkeypatch.setattr(seeder, "get_async_engine", lambda: Engine())

    with pytest.raises(RuntimeError, match="simulated clinical seed failure"):
        await seeder._run()

    assert session.rollback_calls == 1
    assert session.objects == []


@pytest.mark.asyncio
async def test_seed_demo_patient_leaves_unrelated_patients_untouched():
    unrelated = Patient(patient_uuid=uuid.uuid4(), is_deleted=False)
    session = MemorySession([unrelated])

    await seeder.seed_aarav_sharma(session)

    assert unrelated in session.objects
    assert len([obj for obj in session.objects if isinstance(obj, Patient)]) == 2
