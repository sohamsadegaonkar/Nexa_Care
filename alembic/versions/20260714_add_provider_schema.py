"""add provider and NFC registry schema

Revision ID: 20260714_provider_schema
Revises: 20260713_device_key_timestamps
Create Date: 2026-07-14 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_provider_schema"
down_revision: Union[str, None] = "20260713_device_key_timestamps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("hospital_registry"):
        op.create_table(
            "hospital_registry",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("facility_code", sa.String(64), nullable=False),
            sa.Column("legal_name", sa.String(255), nullable=False),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("address_line1", sa.String(255)),
            sa.Column("city", sa.String(128)),
            sa.Column("state", sa.String(128)),
            sa.Column("postal_code", sa.String(32)),
            sa.Column("country_code", sa.String(2), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("facility_code", name="uq_hospital_registry_facility_code"),
        )
        op.create_index("ix_hospital_registry_is_active", "hospital_registry", ["is_active"])
        op.create_index("ix_hospital_registry_facility_code", "hospital_registry", ["facility_code"])

    if not inspector.has_table("provider_identity"):
        op.create_table(
            "provider_identity",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("provider_uid", sa.String(64), unique=True),
            sa.Column("hospital_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hospital_registry.id", ondelete="SET NULL")),
            sa.Column("role", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("display_name", sa.String(255)),
            sa.Column("medical_registration_number", sa.String(64), unique=True),
            sa.Column("specialty", sa.String(128)),
            sa.Column("contact_email", sa.String(320), unique=True),
            sa.Column("contact_phone", sa.String(32)),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        for name, column in (
            ("ix_provider_identity_provider_uid", "provider_uid"),
            ("ix_provider_identity_hospital_id", "hospital_id"),
            ("ix_provider_identity_status", "status"),
            ("ix_provider_identity_is_active", "is_active"),
            ("ix_provider_identity_contact_email", "contact_email"),
        ):
            op.create_index(name, "provider_identity", [column])

    if not inspector.has_table("provider_hospital_affiliation"):
        op.create_table(
            "provider_hospital_affiliation",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("provider_identity.id", ondelete="CASCADE"), nullable=False),
            sa.Column("hospital_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hospital_registry.id", ondelete="CASCADE"), nullable=False),
            sa.Column("affiliation_type", sa.String(32), nullable=False),
            sa.Column("department", sa.String(128)),
            sa.Column("roles", postgresql.JSONB(), nullable=False),
            sa.Column("is_primary", sa.Boolean(), nullable=False),
            sa.Column("valid_from", sa.DateTime(timezone=True)),
            sa.Column("valid_until", sa.DateTime(timezone=True)),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("provider_id", "hospital_id", name="uq_provider_hospital_affiliation"),
        )
        op.create_index("ix_provider_hospital_affiliation_provider_id", "provider_hospital_affiliation", ["provider_id"])
        op.create_index("ix_provider_hospital_affiliation_hospital_id", "provider_hospital_affiliation", ["hospital_id"])
        op.create_index("ix_provider_hospital_affiliation_is_active", "provider_hospital_affiliation", ["is_active"])

    if not inspector.has_table("provider_credential"):
        op.create_table(
            "provider_credential",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("provider_uid", sa.String(64), unique=True),
            sa.Column("hashed_password", sa.Text()),
            sa.Column("mfa_secret", sa.Text()),
            sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("provider_identity.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("login_identifier", sa.String(320), nullable=False, unique=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("mfa_enabled", sa.Boolean(), nullable=False),
            sa.Column("mfa_secret_encrypted", sa.Text()),
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
            sa.Column("locked_until", sa.DateTime(timezone=True)),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_provider_credential_provider_uid", "provider_credential", ["provider_uid"])
        op.create_index("ix_provider_credential_login_identifier", "provider_credential", ["login_identifier"])
        op.create_index("ix_provider_credential_is_active", "provider_credential", ["is_active"])

    if not inspector.has_table("nfc_card_registry"):
        op.create_table(
            "nfc_card_registry",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("card_uid", sa.String(128), nullable=False),
            sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("issued_by", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("card_uid", name="uq_nfc_card_registry_card_uid"),
            sa.CheckConstraint("status IN ('active', 'reported_lost', 'revoked', 'replaced')", name="ck_nfc_card_registry_status"),
        )
        op.create_index("ix_nfc_card_registry_card_uid", "nfc_card_registry", ["card_uid"])
        op.create_index("ix_nfc_card_registry_patient_id", "nfc_card_registry", ["patient_id"])
        op.create_index("ix_nfc_card_registry_status", "nfc_card_registry", ["status"])


def downgrade() -> None:
    # Forward-only and non-destructive: these tables may have originated in
    # legacy SQL deployments and can contain provider identities and secrets.
    pass
