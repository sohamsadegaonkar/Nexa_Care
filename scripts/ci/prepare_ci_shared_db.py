"""Prepare shared disposable PostgreSQL database for CI qualification.

CI qualification baseline only, not production deployment specification.
"""

from __future__ import annotations

import asyncio
import os
import sys

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.helpers.qualification_infra import (  # noqa: E402
    create_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

SHARED_DB_NAME = "nexa_qual_ci_shared"
HEAD = "20260906_verification_scheduler"


def main() -> int:
    os.environ.setdefault("NEXA_ALLOW_DISPOSABLE_TEST_DB", "1")
    db_url = postgres_database_url(SHARED_DB_NAME)
    print(f"Provisioning disposable shared database: {SHARED_DB_NAME}")
    asyncio.run(create_disposable_database(SHARED_DB_NAME))
    print(f"Migrating {SHARED_DB_NAME} to target HEAD {HEAD}...")
    migrate_database_to_head(db_url, target_head=HEAD)
    print("Pre-migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
