"""Add durable clinical conflicts and source supersession provenance.

Revision ID: 20260814_conflict_supersession
Revises: 20260812_dek_store_runtime
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260814_conflict_supersession"
down_revision = "20260812_dek_store_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_candidates",
        sa.Column("clinical_fact_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_extraction_candidates_clinical_fact",
        "extraction_candidates",
        ["clinical_fact_key"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_extraction_candidates_id_evidence",
        "extraction_candidates",
        ["id", "evidence_id"],
    )

    op.create_table(
        "document_source_relationships",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("related_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=16), nullable=False),
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "relation_type IN ('SUPERSEDES', 'ADDENDUM_TO')",
            name="ck_document_source_relationships_type",
        ),
        sa.CheckConstraint(
            "source_document_id <> related_document_id",
            name="ck_document_source_relationships_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["related_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_document_id", name="uq_document_source_relationships_source"
        ),
    )
    op.create_index(
        "ix_document_source_relationships_related",
        "document_source_relationships",
        ["related_document_id"],
        unique=False,
    )

    op.create_table(
        "extraction_conflicts",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("clinical_fact_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "char_length(clinical_fact_key) = 64",
            name="ck_extraction_conflicts_fact_key_length",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["extraction_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "patient_id",
            "job_id",
            "source_document_id",
            name="uq_extraction_conflicts_authoritative_graph",
        ),
        sa.UniqueConstraint(
            "job_id", "clinical_fact_key", name="uq_extraction_conflicts_job_fact"
        ),
    )
    op.create_index(
        "ix_extraction_conflicts_case_graph",
        "extraction_conflicts",
        ["job_id", "source_document_id"],
        unique=False,
    )

    op.create_table(
        "extraction_conflict_members",
        sa.Column("conflict_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id", "evidence_id"],
            ["extraction_candidates.id", "extraction_candidates.evidence_id"],
            name="fk_conflict_member_candidate_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conflict_id"], ["extraction_conflicts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conflict_id", "candidate_id", name="uq_conflict_member_candidate"
        ),
        sa.UniqueConstraint(
            "conflict_id", "evidence_id", name="uq_conflict_member_evidence"
        ),
    )
    op.create_index(
        "ix_extraction_conflict_members_conflict",
        "extraction_conflict_members",
        ["conflict_id"],
        unique=False,
    )

    op.add_column(
        "adjudication_submissions",
        sa.Column(
            "resolved_conflict_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "adjudication_conflict_resolutions",
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conflict_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["adjudication_cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["conflict_id"], ["extraction_conflicts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["adjudication_submissions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "submission_id",
            "conflict_id",
            name="uq_adjudication_conflict_resolution",
        ),
    )
    op.create_index(
        "ix_adjudication_conflict_resolutions_case_conflict",
        "adjudication_conflict_resolutions",
        ["case_id", "conflict_id"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION public.nexa_a1_reject_immutable_provenance_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'A1_PROVENANCE_IMMUTABLE';
        END;
        $$
        """
    )
    for table_name in (
        "document_source_relationships",
        "extraction_conflicts",
        "extraction_conflict_members",
        "adjudication_conflict_resolutions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON public.{table_name}
            FOR EACH ROW
            EXECUTE FUNCTION public.nexa_a1_reject_immutable_provenance_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "adjudication_conflict_resolutions",
        "extraction_conflict_members",
        "extraction_conflicts",
        "document_source_relationships",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable "
            f"ON public.{table_name}"
        )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "public.nexa_a1_reject_immutable_provenance_mutation()"
    )
    op.drop_index(
        "ix_adjudication_conflict_resolutions_case_conflict",
        table_name="adjudication_conflict_resolutions",
    )
    op.drop_table("adjudication_conflict_resolutions")
    op.drop_column("adjudication_submissions", "resolved_conflict_ids")
    op.drop_index(
        "ix_extraction_conflict_members_conflict",
        table_name="extraction_conflict_members",
    )
    op.drop_table("extraction_conflict_members")
    op.drop_index(
        "ix_extraction_conflicts_case_graph", table_name="extraction_conflicts"
    )
    op.drop_table("extraction_conflicts")
    op.drop_index(
        "ix_document_source_relationships_related",
        table_name="document_source_relationships",
    )
    op.drop_table("document_source_relationships")
    op.drop_constraint(
        "uq_extraction_candidates_id_evidence",
        "extraction_candidates",
        type_="unique",
    )
    op.drop_index(
        "ix_extraction_candidates_clinical_fact",
        table_name="extraction_candidates",
    )
    op.drop_column("extraction_candidates", "clinical_fact_key")
