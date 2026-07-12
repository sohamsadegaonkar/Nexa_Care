"""Nexa Care v1.0 Core Schema - Identity, Consent, Tombstones

Revision ID: 20260705_nexa_v1
Revises: previous
Create Date: 2026-07-05

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260705_nexa_v1'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # patients
    op.create_table(
        'patients',
        sa.Column('patient_uuid', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('full_name', sa.Text()),
        sa.Column('date_of_birth', sa.Date()),
        sa.Column('gender', sa.Text()),
        sa.Column('phone', sa.Text()),
        sa.Column('email', sa.Text()),
        sa.Column('abha_id', sa.Text(), unique=True),
        sa.Column('address_line1', sa.Text()),
        sa.Column('address_line2', sa.Text()),
        sa.Column('city', sa.Text()),
        sa.Column('state', sa.Text()),
        sa.Column('pincode', sa.Text()),
        sa.Column('emergency_contact_name', sa.Text()),
        sa.Column('emergency_contact_phone', sa.Text()),
        sa.Column('consent_assurance_policy', sa.Text(), nullable=False, server_default='STANDARD'),
        sa.Column('dek_id', sa.Text()),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.create_index('idx_patients_abha', 'patients', ['abha_id'], postgresql_where=sa.text('abha_id IS NOT NULL'))
    op.create_index('idx_patients_phone', 'patients', ['phone'], postgresql_where=sa.text('phone IS NOT NULL'))

    # patient_external_ids
    op.create_table(
        'patient_external_ids',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('patient_uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('id_type', sa.Text(), nullable=False),
        sa.Column('id_value', sa.Text(), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['patient_uuid'], ['patients.patient_uuid'], ondelete='CASCADE'),
        sa.UniqueConstraint('id_type', 'id_value')
    )

    # card_registry
    op.create_table(
        'card_registry',
        sa.Column('card_id', sa.Text(), primary_key=True),
        sa.Column('patient_uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('card_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.Text(), nullable=False, server_default='ACTIVE'),
        sa.Column('issued_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('last_used_at', sa.TIMESTAMP(timezone=True)),
        sa.ForeignKeyConstraint(['patient_uuid'], ['patients.patient_uuid']),
    )
    op.create_index('idx_card_registry_patient', 'card_registry', ['patient_uuid'])

    # consent_ledger
    op.create_table(
        'consent_ledger',
        sa.Column('consent_id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('patient_uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('hospital_id', sa.Text(), nullable=False),
        sa.Column('clinician_id', sa.Text(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('consent_assurance', sa.Text(), nullable=False),
        sa.Column('granted_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('digital_signature', sa.Text()),
        sa.Column('policy_change_direction', sa.Text()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['patient_uuid'], ['patients.patient_uuid']),
    )
    op.create_index('idx_consent_ledger_patient', 'consent_ledger', ['patient_uuid'])

    # audit_ledger
    op.create_table(
        'audit_ledger',
        sa.Column('audit_id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('patient_uuid', postgresql.UUID(as_uuid=True)),
        sa.Column('actor_type', sa.Text(), nullable=False),
        sa.Column('actor_id', sa.Text(), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('resource', sa.Text()),
        sa.Column('details', postgresql.JSONB()),
        sa.Column('ip_address', postgresql.INET()),
        sa.Column('user_agent', sa.Text()),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('idx_audit_ledger_patient', 'audit_ledger', ['patient_uuid'])
    op.create_index('idx_audit_ledger_timestamp', 'audit_ledger', ['timestamp'])

    # patient_tombstones
    op.create_table(
        'patient_tombstones',
        sa.Column('tombstone_id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('old_patient_uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('canonical_patient_uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('merged_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('merged_by', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text()),
        sa.Column('evidence', postgresql.JSONB()),
        sa.ForeignKeyConstraint(['canonical_patient_uuid'], ['patients.patient_uuid']),
    )
    op.create_index('idx_tombstones_old', 'patient_tombstones', ['old_patient_uuid'])
    op.create_index('idx_tombstones_canonical', 'patient_tombstones', ['canonical_patient_uuid'])

    # consent_sessions
    op.create_table(
        'consent_sessions',
        sa.Column('session_id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('patient_uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('consent_token', sa.Text(), nullable=False, unique=True),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('consent_assurance', sa.Text(), nullable=False),
        sa.Column('issued_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('hospital_id', sa.Text()),
        sa.Column('clinician_id', sa.Text()),
        sa.ForeignKeyConstraint(['patient_uuid'], ['patients.patient_uuid']),
    )
    op.create_index('idx_consent_sessions_token', 'consent_sessions', ['consent_token'])

    # Tables used by the application and by later Alembic revisions.  Older
    # deployments may already have created these through the legacy SQL
    # bootstrap files, so preserve those tables when present.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('nexa_vault'):
        op.create_table(
            'nexa_vault',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
            sa.Column('masked_internal_id', sa.String(64), nullable=False, unique=True),
            sa.Column('patient_name', sa.String(255)),
            sa.Column('phone', sa.String(32)),
            sa.Column('aadhaar_abha_id', sa.String(64)),
            sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        )
        op.create_index('ix_nexa_vault_masked_internal_id', 'nexa_vault', ['masked_internal_id'])
    if not inspector.has_table('nexa_clinical'):
        op.create_table(
            'nexa_clinical',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
            sa.Column('masked_internal_id', sa.String(64), nullable=False),
            sa.Column('diagnoses', postgresql.JSONB()),
            sa.Column('lab_results', postgresql.JSONB()),
            sa.Column('prescriptions', postgresql.JSONB()),
            sa.Column('clinical_data', postgresql.JSONB()),
            sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        )
        op.create_index('ix_nexa_clinical_masked_internal_id', 'nexa_clinical', ['masked_internal_id'])
    if not inspector.has_table('consent_grant_log'):
        op.create_table(
            'consent_grant_log',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
            sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
            sa.Column('patient_id', sa.String(64), nullable=False),
            sa.Column('clinician_id', sa.String(64), nullable=False),
            sa.Column('purpose', sa.String(64), nullable=False),
            sa.Column('scope', postgresql.JSONB(), nullable=False),
            sa.Column('is_break_glass', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('reason_code', sa.String(256)),
            sa.Column('issued_at', sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column('consumed_at', sa.TIMESTAMP(timezone=True)),
            sa.Column('revoked_at', sa.TIMESTAMP(timezone=True)),
            sa.Column('revoked_reason', sa.String(256)),
            sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.CheckConstraint('NOT is_break_glass OR reason_code IS NOT NULL', name='ck_consent_grant_log_break_glass_requires_reason'),
        )
        op.create_index('ix_consent_grant_log_token_hash', 'consent_grant_log', ['token_hash'])
        op.create_index('ix_consent_grant_log_patient_id', 'consent_grant_log', ['patient_id'])
        op.create_index('ix_consent_grant_log_clinician_id', 'consent_grant_log', ['clinician_id'])
    if not inspector.has_table('biometric_registry'):
        op.create_table(
            'biometric_registry',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
            sa.Column('masked_internal_id', postgresql.UUID(as_uuid=True), nullable=False, unique=True),
            sa.Column('bio_verifier_hash', sa.CHAR(64), nullable=False),
            sa.Column('revoked_at', sa.TIMESTAMP(timezone=True)),
            sa.Column('enrolled_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        )
        op.create_index('idx_biometric_registry_patient', 'biometric_registry', ['masked_internal_id'])


def downgrade() -> None:
    # These four tables may predate Alembic (legacy SQL bootstrap).  Do not
    # destroy potentially pre-existing healthcare data during downgrade.
    op.drop_table('consent_sessions')
    op.drop_table('patient_tombstones')
    op.drop_table('audit_ledger')
    op.drop_table('consent_ledger')
    op.drop_table('card_registry')
    op.drop_table('patient_external_ids')
    op.drop_table('patients')
