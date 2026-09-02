from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260903_trust_authorization"


def test_trust_authorization_migration_is_single_head_and_forward_only() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [REVISION]
    revision = scripts.get_revision(REVISION)
    assert revision is not None and revision.down_revision == "20260903_trust_lifecycle"
    source = (ROOT / "alembic" / "versions" / f"{REVISION}.py").read_text()
    for required in (
        "provider_trust_permission_grant",
        "PROFESSIONAL_REVIEW",
        "FACILITY_REVIEW",
        "AFFILIATION_MANAGE",
        "TRUST_PERMISSION_MANAGE",
        "revoked_at IS NULL",
        "Purpose:",
        "Existing-data behavior:",
        "inserts no grants",
        "raise RuntimeError",
    ):
        assert required in source
