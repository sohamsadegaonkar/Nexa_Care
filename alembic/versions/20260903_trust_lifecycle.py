"""Store positive lifecycle generations for provider trust records.

Purpose: add the version field required for future fail-closed lifecycle
application to professional verification, facility verification, and exact
provider-to-facility affiliation records.

Preconditions: upgrade from the sole 20260902_contact_assurance head.  The
Phase-3C policy is pure; this migration does not add endpoints, permissions,
or a compare-and-swap write path.

Existing-data behavior: existing rows receive version 1 before the column is
made non-null.  New rows default to 1.  The database rejects zero or negative
versions.

Locking risk: adding and validating constraints takes PostgreSQL DDL locks;
the bounded UPDATE backfill touches the three existing trust tables.  Run only
through the dedicated release migration task.

Rollback position: forward-only.  Version is lifecycle safety metadata and
must not be removed after application; use a corrective forward revision.

Validation query: SELECT min(version), count(*) FILTER (WHERE version IS NULL
OR version <= 0) FROM each affected table; both must prove only positive,
non-null generations.

Forward-fix strategy: add a new revision rather than rewriting this revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260903_trust_lifecycle"
down_revision = "20260902_contact_assurance"
branch_labels = None
depends_on = None


_TABLES = (
    ("professional_verification", "ck_professional_verification_version_positive"),
    ("facility_verification", "ck_facility_verification_version_positive"),
    (
        "provider_hospital_affiliation",
        "ck_provider_hospital_affiliation_version_positive",
    ),
)


def upgrade() -> None:
    for table, constraint in _TABLES:
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET version = 1 WHERE version IS NULL"))
        op.alter_column(
            table,
            "version",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        )
        op.create_check_constraint(constraint, table, "version > 0")


def downgrade() -> None:
    raise RuntimeError(
        "20260903_trust_lifecycle is forward-only; apply a corrective forward migration instead."
    )
