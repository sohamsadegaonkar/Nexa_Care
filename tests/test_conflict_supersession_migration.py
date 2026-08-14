"""Forward migration contract for durable conflicts and source relations."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260814_conflict_supersession"


def _revision():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_revision(REVISION)


def test_conflict_supersession_is_the_forward_head() -> None:
    revision = _revision()
    assert revision is not None
    assert revision.down_revision == "20260812_dek_store_runtime"
    assert revision.nextrev == frozenset()


def test_conflict_supersession_has_closed_constraints_and_restrictive_fks() -> None:
    content = (ROOT / "alembic" / "versions" / f"{REVISION}.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        "document_source_relationships",
        "extraction_conflicts",
        "extraction_conflict_members",
        "adjudication_conflict_resolutions",
        "SUPERSEDES",
        "ADDENDUM_TO",
        'ondelete="RESTRICT"',
        "uq_extraction_candidates_id_evidence",
        "fk_conflict_member_candidate_evidence",
        "nexa_a1_reject_immutable_provenance_mutation",
        "uq_conflict_member_candidate",
        "uq_adjudication_conflict_resolution",
    ):
        assert marker in content
