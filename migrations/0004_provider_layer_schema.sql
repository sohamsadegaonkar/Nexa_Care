-- 0004_provider_layer_schema.sql
--
-- Phase A: Provider-centric trust infrastructure.
-- Creates hospital registry, provider identity, affiliations, and credentials.
--
-- PRIVACY NOTE
-- ------------
-- provider_identity stores clinician profile data only — never patient PII
-- or clinical records. Authentication secrets are isolated in provider_credential.
--
-- Apply against your Supabase/Postgres instance (SQL editor or migration tool).

-- ── updated_at trigger (reuse if already present) ────────────────────────────

CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── hospital_registry ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS hospital_registry (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    facility_code   VARCHAR(64) NOT NULL,
    legal_name      VARCHAR(255) NOT NULL,
    display_name    VARCHAR(255) NOT NULL,
    address_line1   VARCHAR(255) NULL,
    city            VARCHAR(128) NULL,
    state           VARCHAR(128) NULL,
    postal_code     VARCHAR(32)  NULL,
    country_code    CHAR(2)      NOT NULL DEFAULT 'IN',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT uq_hospital_registry_facility_code UNIQUE (facility_code)
);

CREATE INDEX IF NOT EXISTS ix_hospital_registry_is_active
    ON hospital_registry (is_active);
CREATE INDEX IF NOT EXISTS ix_hospital_registry_facility_code
    ON hospital_registry (facility_code);

DROP TRIGGER IF EXISTS trg_hospital_registry_updated_at ON hospital_registry;
CREATE TRIGGER trg_hospital_registry_updated_at
    BEFORE UPDATE ON hospital_registry
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- ── provider_identity ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS provider_identity (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name                VARCHAR(255) NOT NULL,
    medical_registration_number VARCHAR(64)  NULL,
    specialty                   VARCHAR(128) NULL,
    contact_email               VARCHAR(320) NOT NULL,
    contact_phone               VARCHAR(32)  NULL,
    is_active                   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT uq_provider_identity_medical_registration
        UNIQUE (medical_registration_number),
    CONSTRAINT uq_provider_identity_contact_email
        UNIQUE (contact_email)
);

CREATE INDEX IF NOT EXISTS ix_provider_identity_is_active
    ON provider_identity (is_active);
CREATE INDEX IF NOT EXISTS ix_provider_identity_contact_email
    ON provider_identity (contact_email);

DROP TRIGGER IF EXISTS trg_provider_identity_updated_at ON provider_identity;
CREATE TRIGGER trg_provider_identity_updated_at
    BEFORE UPDATE ON provider_identity
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- ── provider_hospital_affiliation ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS provider_hospital_affiliation (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id       UUID        NOT NULL REFERENCES provider_identity(id) ON DELETE CASCADE,
    hospital_id       UUID        NOT NULL REFERENCES hospital_registry(id) ON DELETE CASCADE,
    affiliation_type  VARCHAR(32) NOT NULL DEFAULT 'permanent',
    department        VARCHAR(128) NULL,
    roles             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    is_primary        BOOLEAN     NOT NULL DEFAULT FALSE,
    valid_from        TIMESTAMPTZ NULL,
    valid_until       TIMESTAMPTZ NULL,
    is_active         BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT uq_provider_hospital_affiliation
        UNIQUE (provider_id, hospital_id)
);

CREATE INDEX IF NOT EXISTS ix_provider_hospital_affiliation_provider_id
    ON provider_hospital_affiliation (provider_id);
CREATE INDEX IF NOT EXISTS ix_provider_hospital_affiliation_hospital_id
    ON provider_hospital_affiliation (hospital_id);
CREATE INDEX IF NOT EXISTS ix_provider_hospital_affiliation_is_active
    ON provider_hospital_affiliation (is_active);

DROP TRIGGER IF EXISTS trg_provider_hospital_affiliation_updated_at ON provider_hospital_affiliation;
CREATE TRIGGER trg_provider_hospital_affiliation_updated_at
    BEFORE UPDATE ON provider_hospital_affiliation
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- ── provider_credential ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS provider_credential (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id           UUID        NOT NULL REFERENCES provider_identity(id) ON DELETE CASCADE,
    login_identifier      VARCHAR(320) NOT NULL,
    password_hash         TEXT        NOT NULL,
    mfa_enabled           BOOLEAN     NOT NULL DEFAULT FALSE,
    mfa_secret_encrypted  TEXT        NULL,
    password_changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    failed_login_attempts INTEGER     NOT NULL DEFAULT 0,
    locked_until          TIMESTAMPTZ NULL,
    is_active             BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_provider_credential_provider_id UNIQUE (provider_id),
    CONSTRAINT uq_provider_credential_login_identifier UNIQUE (login_identifier)
);

CREATE INDEX IF NOT EXISTS ix_provider_credential_login_identifier
    ON provider_credential (login_identifier);
CREATE INDEX IF NOT EXISTS ix_provider_credential_is_active
    ON provider_credential (is_active);

DROP TRIGGER IF EXISTS trg_provider_credential_updated_at ON provider_credential;
CREATE TRIGGER trg_provider_credential_updated_at
    BEFORE UPDATE ON provider_credential
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- Reload PostgREST schema cache when applicable
SELECT pg_notify('pgrst', 'reload schema');
