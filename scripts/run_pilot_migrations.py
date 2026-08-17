#!/usr/bin/env python3
"""Run the approved pilot migration as a one-time release operation."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HEAD = "20260817_failure_quarantine"
ALLOWED_ENVIRONMENTS = frozenset({"pilot", "staging", "production"})
CURRENT_REVISION = re.compile(r"(?m)^\s*([0-9]{8}_[a-z0-9_]+)(?:\s+\(head\))?\s*$")


def repository_heads() -> tuple[str, ...]:
    """Return repository heads without loading runtime configuration."""

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


def _run_alembic(
    arguments: list[str], environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )


def main() -> int:
    environment = os.getenv("ENVIRONMENT", "").strip().lower()
    if environment not in ALLOWED_ENVIRONMENTS:
        print("ERROR: ENVIRONMENT must be pilot, staging, or production")
        return 1

    migration_database_url = os.getenv("MIGRATION_DATABASE_URL", "")
    if not migration_database_url.strip():
        print("ERROR: MIGRATION_DATABASE_URL is required")
        return 1

    try:
        heads = repository_heads()
    except Exception:
        print("ERROR: unable to read the repository migration graph")
        return 1
    if heads != (EXPECTED_HEAD,):
        print("ERROR: repository must have exactly the approved migration head")
        return 1
    print(f"PASS: repository migration head is {EXPECTED_HEAD}")

    child_environment = dict(os.environ)
    child_environment["DATABASE_URL"] = migration_database_url
    child_environment.pop("MIGRATION_DATABASE_URL", None)
    child_environment.pop("TEST_DATABASE_URL", None)
    child_environment.pop("ENV", None)

    try:
        upgrade = _run_alembic(["upgrade", "head"], child_environment)
    except (OSError, subprocess.SubprocessError):
        print("ERROR: migration command could not complete")
        return 1
    if upgrade.returncode != 0:
        print("ERROR: migration command failed; output redacted")
        return 1
    print(f"PASS: database upgraded to {EXPECTED_HEAD}")

    try:
        current = _run_alembic(["current"], child_environment)
    except (OSError, subprocess.SubprocessError):
        print("ERROR: database revision verification could not complete")
        return 1
    if current.returncode != 0:
        print("ERROR: database revision verification failed; output redacted")
        return 1
    revisions = tuple(CURRENT_REVISION.findall(current.stdout))
    if revisions != (EXPECTED_HEAD,):
        print("ERROR: database is not at the approved migration head")
        return 1
    print(f"PASS: database current revision is {EXPECTED_HEAD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
