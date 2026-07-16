"""make provider password_hash canonical and remove legacy ambiguity

Revision ID: 20260717_provider_pwd_canonical
Revises: 20260716_audit_ledger_chain
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_provider_pwd_canonical"
down_revision: Union[str, None] = "20260716_audit_ledger_chain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def resolve_canonical_hash(canonical: str | None, legacy: str | None) -> str:
    """Resolve one row without exposing either hash in errors."""

    canonical_value = canonical.strip() if canonical else ""
    legacy_value = legacy.strip() if legacy else ""
    if canonical_value and legacy_value and canonical_value != legacy_value:
        raise RuntimeError("Conflicting canonical and legacy provider password hashes")
    resolved = canonical_value or legacy_value
    if not resolved:
        raise RuntimeError("Provider credential has no usable password hash")
    return resolved


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("provider_credential")}

    duplicate = bind.execute(sa.text("""
        SELECT lower(btrim(login_identifier)) AS normalized_identifier
        FROM provider_credential
        GROUP BY lower(btrim(login_identifier))
        HAVING count(*) > 1
        LIMIT 1
    """)).first()
    if duplicate is not None:
        raise RuntimeError("Duplicate normalized provider login identifiers require manual resolution")

    if "hashed_password" in columns:
        rows = bind.execute(sa.text("""
            SELECT id, password_hash, hashed_password
            FROM provider_credential
            ORDER BY id
            FOR UPDATE
        """)).mappings()
        for row in rows:
            resolved = resolve_canonical_hash(row["password_hash"], row["hashed_password"])
            if row["password_hash"] != resolved:
                bind.execute(
                    sa.text("UPDATE provider_credential SET password_hash = :value WHERE id = :id"),
                    {"value": resolved, "id": row["id"]},
                )

        op.drop_column("provider_credential", "hashed_password")
    else:
        missing = bind.execute(sa.text("""
            SELECT 1 FROM provider_credential
            WHERE password_hash IS NULL OR btrim(password_hash) = ''
            LIMIT 1
        """)).first()
        if missing is not None:
            raise RuntimeError("Provider credential has no usable password hash")

    bind.execute(sa.text("""
        UPDATE provider_credential
        SET login_identifier = lower(btrim(login_identifier))
        WHERE login_identifier <> lower(btrim(login_identifier))
    """))
    op.alter_column("provider_credential", "password_hash", nullable=False)
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_credential_login_identifier_normalized
        ON provider_credential (lower(btrim(login_identifier)))
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS uq_provider_credential_login_identifier_normalized"
    ))
    op.add_column(
        "provider_credential",
        sa.Column("hashed_password", sa.Text(), nullable=True),
    )
    op.execute(sa.text("""
        UPDATE provider_credential
        SET hashed_password = password_hash
    """))
