"""Static migration contract for Milestone 3 safe lane persistence."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260729_extract_lane_route.py"


def test_milestone_three_has_exact_parent_and_safe_tables():
    code = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260729_extract_lane_route"' in code
    assert 'down_revision = "20260727_doc_process_bind"' in code
    assert code.count("op.create_table(") == 2
    assert '"extraction_decisions"' in code
    assert '"extraction_routing"' in code
    assert "raw_value" not in code
    assert "source_text" not in code
    assert "original_filename" not in code
    assert "AUTO_COMMIT" not in code


def test_migration_has_deliberate_downgrade_without_backfill():
    code = MIGRATION.read_text(encoding="utf-8")
    downgrade = code[code.index("def downgrade") :]
    assert downgrade.count("op.drop_table(") == 2
    assert "UPDATE " not in code
    assert "INSERT " not in code
