from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.nfc_card_registry import NFCCardRegistry
from app.models.provider import (
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260714_provider_schema"
TABLES = (
    HospitalRegistry,
    ProviderIdentity,
    ProviderHospitalAffiliation,
    ProviderCredential,
    NFCCardRegistry,
)


def _source() -> str:
    return (
        ROOT / "alembic" / "versions" / "20260714_add_provider_schema.py"
    ).read_text(encoding="utf-8")


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_provider_revision_precedes_patient_auth_identity_and_current_head() -> None:
    revision = _scripts().get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "20260713_device_key_timestamps"
    assert len(_scripts().get_heads()) == 1


def test_every_doctor_seed_model_has_migration_coverage() -> None:
    source = _source()
    for model in TABLES:
        assert f'"{model.__tablename__}"' in source
        assert f'has_table("{model.__tablename__}")' in source


def test_foreign_key_targets_precede_referencing_tables() -> None:
    source = _source()
    assert source.index('has_table("hospital_registry")') < source.index(
        'has_table("provider_identity")'
    )
    assert source.index('has_table("provider_identity")') < source.index(
        'has_table("provider_hospital_affiliation")'
    )
    assert source.index('has_table("provider_identity")') < source.index(
        'has_table("provider_credential")'
    )


def test_provider_schema_has_model_timestamps_constraints_and_indexes() -> None:
    source = _source()
    assert source.count('sa.Column("created_at"') == 5
    assert source.count('sa.Column("updated_at"') == 5
    for constraint in (
        "uq_hospital_registry_facility_code",
        "uq_provider_hospital_affiliation",
        "uq_nfc_card_registry_card_uid",
        "ck_nfc_card_registry_status",
    ):
        assert constraint in source
    for model in TABLES:
        for index in model.__table__.indexes:
            assert index.name in source


def test_model_and_migration_table_names_match() -> None:
    source = _source()
    expected = {model.__tablename__ for model in TABLES}
    for table in expected:
        assert f'op.create_table(\n            "{table}"' in source


def test_existing_database_upgrade_is_guarded_and_downgrade_is_non_destructive() -> (
    None
):
    source = _source()
    assert source.count("inspector.has_table") == 5
    assert "op.drop_table" not in source
