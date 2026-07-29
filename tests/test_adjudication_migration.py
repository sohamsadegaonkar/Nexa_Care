from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260730_source_adjudicate.py"


def test_adjudication_is_single_head_with_expected_parent():
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260730_source_adjudicate"]
    code = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260730_source_adjudicate"' in code
    assert 'down_revision = "20260729_extract_lane_route"' in code


def test_migration_has_immutable_submission_and_workflow_constraints():
    code = MIGRATION.read_text(encoding="utf-8")
    assert '"adjudication_cases"' in code
    assert '"adjudication_submissions"' in code
    assert "uq_adjudication_submissions_idempotency" in code
    assert "uq_adjudication_submissions_case_attempt" in code
    assert "ck_adjudication_cases_source_binding" in code
    assert "ck_adjudication_submissions_outcome" in code
    assert "clinical_payload" in code
    assert 'drop_table("adjudication_submissions")' in code
