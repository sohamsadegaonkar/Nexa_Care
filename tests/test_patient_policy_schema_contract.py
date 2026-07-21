"""Patient policy ORM and Alembic ownership contracts."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.patient_policy import PatientPolicy


def _scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_policy_table_is_created_before_runtime_columns() -> None:
    creation = _scripts().get_revision("20260718_policy_schema")
    runtime = _scripts().get_revision("20260719_security_runtime")
    assert creation.down_revision == "20260718_security_governance"
    assert runtime.down_revision == creation.revision


def test_patient_policy_orm_contract() -> None:
    table = PatientPolicy.__table__
    assert table.name == "patient_policies"
    assert table.schema is None  # PostgreSQL's default ORM schema is public.
    assert set(table.columns.keys()) == {
        "patient_uuid",
        "tenant_id",
        "consent_assurance_policy",
        "updated_at",
        "version",
        "last_idempotency_key",
    }
    assert table.c.patient_uuid.primary_key
    assert not table.c.patient_uuid.nullable
    assert not table.c.consent_assurance_policy.nullable
    assert not table.c.version.nullable
    assert table.c.tenant_id.type.length == 128
    assert table.c.last_idempotency_key.type.length == 128
    assert {
        foreign_key.target_fullname for foreign_key in table.c.patient_uuid.foreign_keys
    } == {"patients.patient_uuid"}
    assert {constraint.name for constraint in table.constraints} >= {
        "uq_patient_policies_tenant_patient"
    }
