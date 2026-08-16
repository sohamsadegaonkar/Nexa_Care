"""Guard exact human clinical commits and extraction graph bindings.

Revision ID: 20260815_clinical_commit_guard
Revises: 20260815_extract_attempt_events
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_clinical_commit_guard"
down_revision = "20260815_extract_attempt_events"
branch_labels = None
depends_on = None


def _preflight_existing_rows() -> None:
    """Fail closed rather than repairing an already-invalid clinical graph."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM patient_vitals
                WHERE source = 'human_adjudicated'
                  AND source_document_id IS NOT NULL
                GROUP BY patient_id, source_document_id, type, recorded_at
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_DUPLICATE_HUMAN_VITAL_SOURCE_FACT';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM patient_lab_results
                WHERE source = 'human_adjudicated'
                  AND source_document_id IS NOT NULL
                GROUP BY patient_id, source_document_id, test_name, recorded_at
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_DUPLICATE_HUMAN_LAB_SOURCE_FACT';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM timeline_events
                WHERE source = 'human_adjudicated'
                  AND event_ref_id IS NOT NULL
                GROUP BY event_type, event_ref_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_DUPLICATE_HUMAN_TIMELINE_REFERENCE';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM patient_vitals v
                WHERE v.source_document_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM document_storage d
                      WHERE d.id = v.source_document_id
                        AND d.patient_id = v.patient_id
                  )
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_VITAL_SOURCE_PATIENT_BINDING_MISMATCH';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM patient_lab_results l
                WHERE l.source_document_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM document_storage d
                      WHERE d.id = l.source_document_id
                        AND d.patient_id = l.patient_id
                  )
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_LAB_SOURCE_PATIENT_BINDING_MISMATCH';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM extraction_jobs j
                WHERE j.tenant_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM document_storage d
                      WHERE d.id = j.document_id
                        AND d.tenant_id = j.tenant_id
                        AND d.patient_id = j.patient_id
                  )
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_JOB_DOCUMENT_GRAPH_MISMATCH';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM extraction_candidates c
                JOIN extraction_jobs j ON j.id = c.job_id
                WHERE c.tenant_id IS DISTINCT FROM j.tenant_id
                   OR c.patient_id IS DISTINCT FROM j.patient_id
                   OR c.source_document_id IS DISTINCT FROM j.document_id
                  OR NOT EXISTS (
                      SELECT 1 FROM document_storage d
                      WHERE d.id = c.source_document_id
                        AND d.tenant_id = c.tenant_id
                        AND d.patient_id = c.patient_id
                  )
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_CANDIDATE_GRAPH_MISMATCH';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM extraction_decisions d
                JOIN extraction_jobs j ON j.id = d.job_id
                WHERE d.tenant_id IS DISTINCT FROM j.tenant_id
                   OR d.patient_id IS DISTINCT FROM j.patient_id
                   OR d.source_document_id IS DISTINCT FROM j.document_id
                  OR NOT EXISTS (
                      SELECT 1 FROM document_storage s
                      WHERE s.id = d.source_document_id
                        AND s.tenant_id = d.tenant_id
                        AND s.patient_id = d.patient_id
                  )
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_DECISION_GRAPH_MISMATCH';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM extraction_decisions
                WHERE organization_id IS DISTINCT FROM tenant_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_DECISION_ORGANIZATION_TENANT_MISMATCH';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM extraction_routing r
                JOIN extraction_decisions d ON d.id = r.decision_id
                WHERE r.job_id IS DISTINCT FROM d.job_id
                   OR r.tenant_id IS DISTINCT FROM d.tenant_id
                   OR r.patient_id IS DISTINCT FROM d.patient_id
                   OR r.source_document_id IS DISTINCT FROM d.source_document_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'C1_ROUTING_DECISION_GRAPH_MISMATCH';
            END IF;
        END;
        $$;
        """
    )


def upgrade() -> None:
    _preflight_existing_rows()

    op.create_check_constraint(
        "ck_extraction_decisions_organization_tenant",
        "extraction_decisions",
        "organization_id = tenant_id",
    )

    op.create_unique_constraint(
        "uq_document_storage_id_patient",
        "document_storage",
        ["id", "patient_id"],
    )
    op.create_unique_constraint(
        "uq_document_storage_id_tenant_patient",
        "document_storage",
        ["id", "tenant_id", "patient_id"],
    )
    op.create_unique_constraint(
        "uq_extraction_jobs_authoritative_graph",
        "extraction_jobs",
        ["id", "tenant_id", "patient_id", "document_id"],
    )
    op.create_unique_constraint(
        "uq_extraction_decisions_authoritative_graph",
        "extraction_decisions",
        ["id", "job_id", "tenant_id", "patient_id", "source_document_id"],
    )

    op.create_index(
        "uq_patient_vitals_human_source_fact",
        "patient_vitals",
        ["patient_id", "source_document_id", "type", "recorded_at"],
        unique=True,
        postgresql_where=sa.text(
            "source = 'human_adjudicated' AND source_document_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_patient_lab_results_human_source_fact",
        "patient_lab_results",
        ["patient_id", "source_document_id", "test_name", "recorded_at"],
        unique=True,
        postgresql_where=sa.text(
            "source = 'human_adjudicated' AND source_document_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_timeline_events_human_reference",
        "timeline_events",
        ["event_type", "event_ref_id"],
        unique=True,
        postgresql_where=sa.text(
            "source = 'human_adjudicated' AND event_ref_id IS NOT NULL"
        ),
    )

    op.create_foreign_key(
        "fk_extraction_jobs_authoritative_document_graph",
        "extraction_jobs",
        "document_storage",
        ["document_id", "tenant_id", "patient_id"],
        ["id", "tenant_id", "patient_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_patient_vitals_source_patient",
        "patient_vitals",
        "document_storage",
        ["source_document_id", "patient_id"],
        ["id", "patient_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_patient_lab_results_source_patient",
        "patient_lab_results",
        "document_storage",
        ["source_document_id", "patient_id"],
        ["id", "patient_id"],
        ondelete="RESTRICT",
    )

    for table, name, referent, local, remote, delete in (
        (
            "extraction_candidates",
            "fk_extraction_candidates_authoritative_job_graph",
            "extraction_jobs",
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            ["id", "tenant_id", "patient_id", "document_id"],
            "CASCADE",
        ),
        (
            "extraction_candidates",
            "fk_extraction_candidates_authoritative_document_graph",
            "document_storage",
            ["source_document_id", "tenant_id", "patient_id"],
            ["id", "tenant_id", "patient_id"],
            "CASCADE",
        ),
        (
            "extraction_decisions",
            "fk_extraction_decisions_authoritative_job_graph",
            "extraction_jobs",
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            ["id", "tenant_id", "patient_id", "document_id"],
            "RESTRICT",
        ),
        (
            "extraction_decisions",
            "fk_extraction_decisions_authoritative_document_graph",
            "document_storage",
            ["source_document_id", "tenant_id", "patient_id"],
            ["id", "tenant_id", "patient_id"],
            "RESTRICT",
        ),
        (
            "extraction_routing",
            "fk_extraction_routing_authoritative_decision_graph",
            "extraction_decisions",
            [
                "decision_id",
                "job_id",
                "tenant_id",
                "patient_id",
                "source_document_id",
            ],
            ["id", "job_id", "tenant_id", "patient_id", "source_document_id"],
            "RESTRICT",
        ),
        (
            "extraction_routing",
            "fk_extraction_routing_authoritative_job_graph",
            "extraction_jobs",
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            ["id", "tenant_id", "patient_id", "document_id"],
            "RESTRICT",
        ),
        (
            "extraction_routing",
            "fk_extraction_routing_authoritative_document_graph",
            "document_storage",
            ["source_document_id", "tenant_id", "patient_id"],
            ["id", "tenant_id", "patient_id"],
            "RESTRICT",
        ),
    ):
        op.create_foreign_key(
            name,
            table,
            referent,
            local,
            remote,
            ondelete=delete,
        )


def downgrade() -> None:
    op.drop_constraint(
        "ck_extraction_decisions_organization_tenant",
        table_name="extraction_decisions",
        type_="check",
    )
    for table, name in (
        ("extraction_routing", "fk_extraction_routing_authoritative_document_graph"),
        ("extraction_routing", "fk_extraction_routing_authoritative_job_graph"),
        ("extraction_routing", "fk_extraction_routing_authoritative_decision_graph"),
        (
            "extraction_decisions",
            "fk_extraction_decisions_authoritative_document_graph",
        ),
        ("extraction_decisions", "fk_extraction_decisions_authoritative_job_graph"),
        (
            "extraction_candidates",
            "fk_extraction_candidates_authoritative_document_graph",
        ),
        ("extraction_candidates", "fk_extraction_candidates_authoritative_job_graph"),
        ("patient_lab_results", "fk_patient_lab_results_source_patient"),
        ("patient_vitals", "fk_patient_vitals_source_patient"),
        ("extraction_jobs", "fk_extraction_jobs_authoritative_document_graph"),
    ):
        op.drop_constraint(name, table_name=table, type_="foreignkey")
    op.drop_index("uq_timeline_events_human_reference", table_name="timeline_events")
    op.drop_index(
        "uq_patient_lab_results_human_source_fact", table_name="patient_lab_results"
    )
    op.drop_index("uq_patient_vitals_human_source_fact", table_name="patient_vitals")
    op.drop_constraint(
        "uq_extraction_decisions_authoritative_graph",
        table_name="extraction_decisions",
        type_="unique",
    )
    op.drop_constraint(
        "uq_extraction_jobs_authoritative_graph",
        table_name="extraction_jobs",
        type_="unique",
    )
    op.drop_constraint(
        "uq_document_storage_id_tenant_patient",
        table_name="document_storage",
        type_="unique",
    )
    op.drop_constraint(
        "uq_document_storage_id_patient",
        table_name="document_storage",
        type_="unique",
    )
