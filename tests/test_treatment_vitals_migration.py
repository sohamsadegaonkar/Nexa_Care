from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260918_treatment_vitals_encounter"
PARENT = "20260918_canonical_encounter"


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_treatment_vitals_migration_descends_linearly_from_canonical_encounter():
    revision = _scripts().get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PARENT


def test_treatment_vitals_migration_is_nullable_no_backfill_encounter_binding():
    source = (
        ROOT / "alembic" / "versions" / f"{REVISION}.py"
    ).read_text(encoding="utf-8")

    for required in (
        '"patient_vitals"',
        '"encounter_id"',
        '"clinical_encounters"',
        '"fk_patient_vitals_encounter"',
        '"ix_patient_vitals_encounter_id"',
        'nullable=True',
        'ondelete="RESTRICT"',
        "Existing-data behavior:",
        "No clinical row values",
        "Forward-fix strategy:",
    ):
        assert required in source

    lowered = source.lower()
    assert "update patient_vitals" not in lowered
    assert "insert into patient_vitals" not in lowered
    assert "merge revision" not in lowered
