"""Validate staging connections, then use the canonical API entrypoint."""

import os
import sys

from scripts.azure_staging_targets import check_database, check_redis


def main() -> int:
    try:
        check_database(os.environ, migration=False)
        check_redis(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    os.execv("/bin/sh", ["/bin/sh", "/app/scripts/container_start.sh"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
