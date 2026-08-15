"""Add immutable, value-free provider lifecycle attempt events.

Revision ID: 20260815_extract_attempt_events
Revises: 20260814_conflict_supersession
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260815_extract_attempt_events"
down_revision = "20260814_conflict_supersession"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_attempt_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_subattempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_adapter", sa.String(length=32), nullable=False),
        sa.Column("provider_contract_version", sa.String(length=64), nullable=False),
        sa.Column("provider_model_version", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("response_complete", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "job_attempt_number >= 1",
            name="ck_extraction_attempt_events_job_attempt_positive",
        ),
        sa.CheckConstraint(
            "provider_subattempt_number >= 1",
            name="ck_extraction_attempt_events_provider_subattempt_positive",
        ),
        sa.CheckConstraint(
            "outcome IN ('SUCCEEDED', 'TIMEOUT', 'THROTTLED', "
            "'RETRYABLE_ERROR', 'INVALID_DOCUMENT', 'INVALID_RESPONSE', "
            "'CREDENTIALS_UNAVAILABLE')",
            name="ck_extraction_attempt_events_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'SUCCEEDED' AND response_complete = true "
            "AND error_code IS NULL) OR "
            "(outcome <> 'SUCCEEDED' AND response_complete = false)",
            name="ck_extraction_attempt_events_completion",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["extraction_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "job_attempt_number",
            "provider_subattempt_number",
            name="uq_extraction_attempt_events_logical_identity",
        ),
    )
    op.create_index(
        "ix_extraction_attempt_events_job_attempt",
        "extraction_attempt_events",
        ["job_id", "job_attempt_number"],
        unique=False,
    )
    op.create_index(
        "ix_extraction_attempt_events_tenant_patient_job",
        "extraction_attempt_events",
        ["tenant_id", "patient_id", "job_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION public.nexa_b1_validate_attempt_event_binding()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            job_tenant_id uuid;
            job_patient_id uuid;
            job_document_id uuid;
            document_tenant_id uuid;
            document_patient_id uuid;
        BEGIN
            SELECT tenant_id, patient_id, document_id
            INTO job_tenant_id, job_patient_id, job_document_id
            FROM public.extraction_jobs
            WHERE id = NEW.job_id
            FOR KEY SHARE;

            IF NOT FOUND
                OR NEW.tenant_id IS DISTINCT FROM job_tenant_id
                OR NEW.patient_id IS DISTINCT FROM job_patient_id
                OR NEW.source_document_id IS DISTINCT FROM job_document_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'B1_ATTEMPT_EVENT_BINDING_MISMATCH';
            END IF;

            SELECT tenant_id, patient_id
            INTO document_tenant_id, document_patient_id
            FROM public.document_storage
            WHERE id = NEW.source_document_id
            FOR KEY SHARE;

            IF NOT FOUND
                OR document_tenant_id IS DISTINCT FROM NEW.tenant_id
                OR document_patient_id IS DISTINCT FROM NEW.patient_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'B1_ATTEMPT_EVENT_DOCUMENT_BINDING_MISMATCH';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_extraction_attempt_events_binding
        BEFORE INSERT ON public.extraction_attempt_events
        FOR EACH ROW
        EXECUTE FUNCTION public.nexa_b1_validate_attempt_event_binding()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.nexa_b1_reject_attempt_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'B1_ATTEMPT_EVENT_IMMUTABLE';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_extraction_attempt_events_immutable
        BEFORE UPDATE OR DELETE ON public.extraction_attempt_events
        FOR EACH ROW
        EXECUTE FUNCTION public.nexa_b1_reject_attempt_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_extraction_attempt_events_binding "
        "ON public.extraction_attempt_events"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.nexa_b1_validate_attempt_event_binding()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_extraction_attempt_events_immutable "
        "ON public.extraction_attempt_events"
    )
    op.execute("DROP FUNCTION IF EXISTS public.nexa_b1_reject_attempt_event_mutation()")
    op.drop_index(
        "ix_extraction_attempt_events_tenant_patient_job",
        table_name="extraction_attempt_events",
    )
    op.drop_index(
        "ix_extraction_attempt_events_job_attempt",
        table_name="extraction_attempt_events",
    )
    op.drop_table("extraction_attempt_events")
