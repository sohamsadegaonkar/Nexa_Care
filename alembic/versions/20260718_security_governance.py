"""audit integrity metadata and plaintext patient PII write barrier

Revision ID: 20260718_security_governance
Revises: 20260717_secure_doc_pipe
"""

from alembic import op
import sqlalchemy as sa

revision = "20260718_security_governance"
down_revision = "20260717_secure_doc_pipe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_ledger", sa.Column("chain_scope", sa.String(64), nullable=False, server_default="global"), schema="public")
    op.add_column("audit_ledger", sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1"), schema="public")
    op.add_column("audit_ledger", sa.Column("idempotency_key", sa.String(64), nullable=True), schema="public")
    op.create_index("ix_audit_ledger_chain_scope", "audit_ledger", ["chain_scope"], schema="public")
    op.create_index(
        "uq_audit_ledger_scope_idempotency",
        "audit_ledger",
        ["chain_scope", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        schema="public",
    )
    op.create_check_constraint(
        "ck_audit_ledger_protocol_version_positive",
        "audit_ledger",
        "protocol_version >= 1",
        schema="public",
    )

    # Staged PII cleanup: existing rows are inspected/migrated separately, but
    # PostgreSQL immediately rejects every new plaintext write.  The constraint
    # is NOT VALID so legacy non-null rows do not make deployment destructive.
    op.execute(
        """
        ALTER TABLE public.patients ADD CONSTRAINT ck_patients_no_plaintext_pii
        CHECK (
            full_name IS NULL AND date_of_birth IS NULL AND gender IS NULL
            AND phone IS NULL AND email IS NULL AND abha_id IS NULL
            AND address_line1 IS NULL AND address_line2 IS NULL AND city IS NULL
            AND state IS NULL AND pincode IS NULL
            AND emergency_contact_name IS NULL AND emergency_contact_phone IS NULL
        ) NOT VALID
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_patients_no_plaintext_pii", "patients", type_="check", schema="public")
    op.drop_constraint("ck_audit_ledger_protocol_version_positive", "audit_ledger", type_="check", schema="public")
    op.drop_index("uq_audit_ledger_scope_idempotency", table_name="audit_ledger", schema="public")
    op.drop_index("ix_audit_ledger_chain_scope", table_name="audit_ledger", schema="public")
    op.drop_column("audit_ledger", "idempotency_key", schema="public")
    op.drop_column("audit_ledger", "protocol_version", schema="public")
    op.drop_column("audit_ledger", "chain_scope", schema="public")
