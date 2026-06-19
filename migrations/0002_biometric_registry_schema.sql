-- 0002_biometric_registry_schema.sql
--
-- Creates the biometric_registry table consumed by
-- app/services/biometric_registry.py.
--
-- WHY THIS MIGRATION IS NEEDED
-- ----------------------------
-- The smoke test failure chain is:
--
--   POST /api/v1/enroll-biometric -> 502 Bad Gateway
--   POST /api/v1/handshake        -> 401 Unauthorized
--
-- Both failures trace to a single root cause: this table does not exist
-- yet in Supabase.  enroll_biometric_binding() catches the PostgREST
-- "relation does not exist" APIError in its bare `except`, returns False,
-- and enroll_biometric_binding_with_audit() correctly raises HTTP 502.
-- With nothing enrolled, verify_biometric_binding() also fails closed
-- (correct behavior, wrong reason), so the handshake never issues a
-- session token.
--
-- PRIVACY / DPDP NOTE
-- -------------------
-- This table stores NO raw biometric data and NO plaintext nfc_uid.
-- The only credential-related column is bio_verifier_hash, which is a
-- server-side peppered HMAC (HMAC-SHA256 keyed by HANDSHAKE_PEPPER_SECRET)
-- of the concatenated nfc_uid and bio_seed.  It is computationally
-- infeasible to reverse to either input without the pepper, and the pepper
-- is held only in environment/secrets config, never in the database.
-- This satisfies ABDM's requirement that biometric templates not be stored
-- natively and DPDP's data-minimisation principle.
--
-- SCHEMA DECISIONS
-- ----------------
-- * UNIQUE(masked_internal_id): one active binding per patient.  A second
--   enroll attempt for the same patient correctly hits a unique_violation
--   and returns the "may already be enrolled" 502.  Re-enrollment requires
--   revoking the existing row first (revoked_at IS NOT NULL) — this is an
--   intentional operational guardrail, not a bug.
--
-- * revoked_at TIMESTAMPTZ NULL: soft-delete.  A revoked binding is kept
--   for the audit trail (so we can prove when access was cut off) but
--   verify_biometric_binding() returns False for any row where this is
--   non-NULL, even if the verifier still matches.
--
-- * nfc_uid is NOT stored here.  The verifier already encodes the device
--   identity via the HMAC; storing nfc_uid separately would create a
--   second PII surface with no security benefit.
--
-- * The UNIQUE constraint is on masked_internal_id, not bio_verifier_hash.
--   Two patients could theoretically produce the same verifier (birthday
--   collision) — constraining on the patient ID is the right key.
--
-- ROW LEVEL SECURITY
-- ------------------
-- RLS is enabled and all access is locked to the service role.  No patient
-- session (JWT from Supabase Auth) can read or write this table.  Only the
-- backend process — authenticated as the service role via SUPABASE_KEY —
-- can insert or select.  This matches the verify_provider_token gate on
-- /api/v1/enroll-biometric at the application layer.
--
-- Apply this against your Supabase instance (SQL editor or migration tool).
-- It has not been run in this sandbox (no live DB connection here).

-- ── Table ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS biometric_registry (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Links this binding to the patient's PII vault and clinical shard.
    -- The application layer enforces that this is always a valid UUID from
    -- nexa_vault; no FK is defined here to keep the PII and biometric stores
    -- independently deployable (and to avoid cross-shard FK constraints).
    masked_internal_id  UUID        NOT NULL,

    -- Peppered HMAC-SHA256 of "{nfc_uid}:{bio_seed}".
    -- keyed by HANDSHAKE_PEPPER_SECRET.  Never reversible without the pepper.
    -- Length is always 64 hex characters (SHA-256 output).
    bio_verifier_hash   CHAR(64)    NOT NULL,

    -- Soft-delete: set by a revocation action, never by the enrollment path.
    -- verify_biometric_binding() treats any non-NULL value as revoked.
    revoked_at          TIMESTAMPTZ NULL DEFAULT NULL,

    -- Immutable enrollment timestamp.
    enrolled_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Standard audit columns.
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Constraints ──────────────────────────────────────────────────────────────

-- One active binding per patient.  A second enroll attempt for the same
-- masked_internal_id raises a unique_violation (23505), which
-- enroll_biometric_binding() catches and surfaces as HTTP 502 with the
-- "may already be enrolled" message.  This is the intended operational
-- behaviour — re-enrollment must go through a revocation step first.
ALTER TABLE biometric_registry
    ADD CONSTRAINT uq_biometric_registry_patient
    UNIQUE (masked_internal_id);

-- ── Indexes ──────────────────────────────────────────────────────────────────

-- verify_biometric_binding() selects by masked_internal_id on every handshake.
-- The UNIQUE constraint above already creates a unique index, but naming it
-- explicitly makes query-plan inspection easier.
CREATE INDEX IF NOT EXISTS idx_biometric_registry_patient
    ON biometric_registry (masked_internal_id);

-- ── updated_at trigger ───────────────────────────────────────────────────────

-- Keep updated_at current on any row mutation (e.g. revocation).
CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_biometric_registry_updated_at
    BEFORE UPDATE ON biometric_registry
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- ── Row Level Security ───────────────────────────────────────────────────────

-- Enable RLS so that no Supabase Auth JWT (patient-facing tokens) can touch
-- this table.  Only the service role (the backend process authenticated via
-- SUPABASE_KEY) can read or write biometric bindings.
ALTER TABLE biometric_registry ENABLE ROW LEVEL SECURITY;

-- Explicit DENY for authenticated users (belt-and-suspenders: RLS with no
-- permissive policy already blocks them, but the explicit policy documents
-- intent and survives policy-ordering surprises).
CREATE POLICY biometric_registry_service_role_only
    ON biometric_registry
    FOR ALL
    TO authenticated        -- Supabase Auth users (patients via JWT)
    USING (false);          -- Never allowed, regardless of operation.

-- The service role bypasses RLS by default in Supabase — no additional
-- policy is needed.  If your project disables that default, add:
-- CREATE POLICY biometric_registry_service_role_rw
--     ON biometric_registry FOR ALL TO service_role USING (true);

-- ── Completion note ──────────────────────────────────────────────────────────

-- After applying this migration, re-run the smoke test:
--
--   BASE_URL=https://your-instance CLINIC_API_KEY=... python3 scripts/smoke_test.py
--
-- Expected result: POST /api/v1/enroll-biometric -> 201, POST /api/v1/handshake -> 200.