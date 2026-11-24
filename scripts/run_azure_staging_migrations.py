"""Invoke the existing pinned migration runner on the isolated Azure database."""

import os
import sys

from scripts import run_pilot_migrations
from scripts.azure_staging_targets import check_database


def main() -> int:
    try:
        check_database(os.environ, migration=True)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    os.environ["NEXA_AZURE_STAGING_MIGRATION_LOCK"] = "1"
    return run_pilot_migrations.main()


if __name__ == "__main__":
    raise SystemExit(main())
