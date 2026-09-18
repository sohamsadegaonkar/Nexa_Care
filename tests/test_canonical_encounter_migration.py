from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260918_canonical_encounter"
PARENT = "20260916_patient_external_record_import"


def test_canonical_encounter_migration_preserves_linear_ancestry():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PARENT


def test_canonical_encounter_migration_is_minimal_and_authority_bound():
    source = (
        ROOT / "alembic" / "versions" / f"{REVISION}.py"
    ).read_text(encoding="utf-8")
    for required in (
        '"clinical_encounters"',
        '"encounter_id"',
        '"clinical_session_id"',
        '"patient_id"',
        '"provider_id"',
        '"hospital_id"',
        '"uq_clinical_encounter_session"',
        '"fk_clinical_encounter_session"',
        '"fk_clinical_encounter_patient"',
        '"fk_clinical_encounter_provider"',
        '"fk_clinical_encounter_hospital"',
        'Purpose:',
        'Preconditions:',
        'Existing-data behavior:',
        'Locking risk:',
        'Rollback position:',
        'Validation query:',
        'Forward-fix strategy:',
    ):
        assert required in source

    for forbidden_column in (
        'sa.Column("diagnosis"',
        'sa.Column("notes"',
        'sa.Column("vitals"',
        'sa.Column("prescription"',
        'sa.Column("assessment"',
        'sa.Column("plan"',
    ):
        assert forbidden_column not in source
