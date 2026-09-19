from app.models.base import Base
from app.models.medication_catalog import (
    MedicationCatalogEmergencyDeny,
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
)
from app.models.provider import ProviderTrustPermissionGrant


def _constraint_sql(table) -> str:
    return "\n".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    )


def test_catalog_tables_are_registered_without_prescription_tables() -> None:
    names = {table.name for table in Base.metadata.sorted_tables}
    assert {
        "medication_catalog_release",
        "medication_catalog_entry",
        "medication_catalog_evidence",
        "medication_catalog_emergency_deny",
    } <= names
    assert "prescription" not in names
    assert "prescription_item" not in names


def test_release_model_has_single_active_index_and_closed_lifecycle() -> None:
    table = MedicationCatalogRelease.__table__
    indexes = {index.name: index for index in table.indexes}
    assert "uq_medication_catalog_release_single_active" in indexes
    assert indexes["uq_medication_catalog_release_single_active"].unique is True
    sql = _constraint_sql(table)
    for state in ("DRAFT", "QUALIFIED", "ACTIVE", "SUPERSEDED", "REVOKED"):
        assert state in sql
    assert "ECDSA_SHA_256" in sql


def test_entry_model_has_closed_classifications_and_release_code_uniqueness() -> None:
    table = MedicationCatalogEntry.__table__
    sql = _constraint_sql(table)
    for required in (
        "NONE_CONFIRMED",
        "H1",
        "LIST_O_ANY_MODE",
        "NOT_CONTROLLED_CONFIRMED",
        "SPECIALIST_RESTRICTED",
        "ONCOLOGY_HIGH_RISK",
        "CURRENT",
        "UNKNOWN",
    ):
        assert required in sql
    unique_names = {item.name for item in table.constraints if item.name}
    assert "uq_medication_catalog_entry_release_code" in unique_names
    assert table.c.medication_code.type.length == 64
    assert table.c.v1_universal_allowed.nullable is False


def test_evidence_and_emergency_models_are_bounded() -> None:
    evidence_sql = _constraint_sql(MedicationCatalogEvidence.__table__)
    emergency_sql = _constraint_sql(MedicationCatalogEmergencyDeny.__table__)
    assert "SNOMED_IDENTITY_ONLY" in evidence_sql
    assert "REGULATORY_PRODUCT_STATUS" in evidence_sql
    assert "DENY" in emergency_sql
    assert "CLEAR" in emergency_sql
    assert "PATIENT_SAFETY_HOLD" in emergency_sql
    assert "ALLOW" not in emergency_sql


def test_catalog_permission_is_global_and_not_clinical_role_authority() -> None:
    sql = _constraint_sql(ProviderTrustPermissionGrant.__table__)
    assert "MEDICATION_CATALOG_RELEASE_REVIEW" in sql
    assert "scope_type = 'GLOBAL'" in sql
