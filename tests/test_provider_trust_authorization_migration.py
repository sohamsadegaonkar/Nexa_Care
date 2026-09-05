from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260903_trust_authorization"
APPLICATION_REVISION = "20260905_verification_application"
HEAD_REVISION = "20260906_verification_scheduler"


def test_trust_authorization_migration_is_single_head_and_forward_only() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [HEAD_REVISION]
    revision = scripts.get_revision(REVISION)
    assert revision is not None and revision.down_revision == "20260903_trust_lifecycle"
    evidence_revision = scripts.get_revision("20260904_verification_evidence")
    assert evidence_revision is not None and evidence_revision.down_revision == REVISION
    app_revision = scripts.get_revision(APPLICATION_REVISION)
    assert (
        app_revision is not None
        and app_revision.down_revision == "20260904_verification_evidence"
    )
    head_revision = scripts.get_revision(HEAD_REVISION)
    assert (
        head_revision is not None
        and head_revision.down_revision == APPLICATION_REVISION
    )
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
