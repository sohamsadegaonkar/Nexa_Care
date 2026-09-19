from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts import run_pilot_migrations

ROOT = Path(__file__).resolve().parents[1]


def test_pilot_expected_head_equals_repository_single_alembic_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    heads = tuple(ScriptDirectory.from_config(config).get_heads())
    assert heads == (run_pilot_migrations.EXPECTED_HEAD,)
