-- 0003_biometric_registry_align_schema.sql
--
-- Aligns the pre-existing biometric_registry table with the column names
-- expected by app/services/biometric_registry.py.
--
-- WHY THIS EXISTS
-- ---------------
-- biometric_registry was created manually before the codebase was finalised,
-- using column names that diverged from the schema documented in
-- migrations/0002_biometric_registry_schema.sql.  The result was:
--
--   POST /api/v1/enroll-biometric -> 502
--     PGRST204: Could not find the 'bio_verifier_hash' column
--   POST /api/v1/handshake -> 401
--     42703: column biometric_registry.bio_verifier_hash does not exist
--
-- The live table had:
--   nfc_uid       TEXT NOT NULL   -- PII; must not be stored (DPDP violation)
--   patient_id    TEXT NOT NULL   -- should be: masked_internal_id
--   bio_seed_hash TEXT NOT NULL   -- should be: bio_verifier_hash
--   created_at    TSTZ            -- correct; no change needed
--
-- CHANGES IN THIS MIGRATION
-- -------------------------
-- 1. RENAME patient_id    -> masked_internal_id
--       Code inserts/selects this key by name.
--
-- 2. RENAME bio_seed_hash -> bio_verifier_hash
--       Code selects 'bio_verifier_hash,revoked_at' and reads
--       row.get('bio_verifier_hash').  The old name was also semantically
--       wrong: we store a server-side peppered HMAC (keyed by
--       HANDSHAKE_PEPPER_SECRET), not a raw hash of bio_seed.
--
-- 3. DROP nfc_uid
--       nfc_uid is a biometric device identifier and constitutes personal
--       data under India's DPDP Act 2023.  Storing it in plaintext here is
--       a compliance violation with no security justification: the verifier
--       column (bio_verifier_hash) already encodes device identity as part of
--       its HMAC input (message = "{nfc_uid}:{bio_seed}"), so nfc_uid can
--       be reconstructed at verification time without ever persisting it.
--       This column must be dropped.
--
-- 4. ADD revoked_at TIMESTAMPTZ NULL
--       Required by verify_biometric_binding(), which gates access on
--       'if row.get("revoked_at"): return False'.  Without this column the
--       SELECT returns a row that can never be soft-revoked.
--
-- 5. ADD id UUID PRIMARY KEY
--       The original table had no primary key.  A missing PK is incompatible
--       with Supabase's Row Level Security machinery and makes it impossible
--       to reference individual rows safely (e.g. for revocation).
--
-- 6. ADD UNIQUE(masked_internal_id)
--       Enforces one active binding per patient.  A second enroll attempt
--       for the same masked_internal_id raises unique_violation (23505),
--       which enroll_biometric_binding() logs and surfaces as HTTP 502 with
--       the "may already be enrolled" message.  Re-enrollment requires an
--       explicit revocation step.
--
-- 7. Schema cache reload
--       PostgREST caches the DB schema at startup.  After any ALTER TABLE
--       the cache must be explicitly invalidated; otherwise PGRST204 errors
--       continue until the next PostgREST restart.
--
-- SAFETY PROPERTIES
-- -----------------
-- * The entire migration runs inside a single transaction.  If any step
--   fails, none of the changes are committed.
-- * RENAME COLUMN is non-destructive: existing data is preserved.
-- * DROP COLUMN nfc_uid is destructive, intentionally so: this PII must
--   not exist in this table.  If you need an audit trail of which device
--   was used during enrollment, record that event in system_audit instead
--   (the BIOMETRIC_ENROLLMENT_ATTEMPT log entry already captures the actor).
-- * The UNIQUE constraint may fail if duplicate patient_id values already
--   exist in the table.  See the pre-flight check below.
--
-- PRE-FLIGHT CHECK (run separately, do not include in this transaction)
-- ───────────────────────────────────────────────────────────────────────
-- Run this before applying the migration to catch data problems early:
--
--   -- 1. Any data in the table at all?
--   SELECT COUNT(*) FROM biometric_registry;
--
--   -- 2. Duplicate patient_id values that would block the UNIQUE constraint?
--   SELECT patient_id, COUNT(*)
--   FROM biometric_registry
--   GROUP BY patient_id
--   HAVING COUNT(*) > 1;
--
--   -- 3. Inspect any rows present (to decide whether to truncate or migrate)
--   SELECT * FROM biometric_registry LIMIT 20;
--
-- If the table is empty or contains only test data, TRUNCATE is the cleanest
-- approach before applying this migration.  If it contains real enrollment
-- data, each duplicate patient_id group must be resolved manually first
-- (keep the most recent row, delete the others) before the UNIQUE constraint
-- can be added.
-- ───────────────────────────────────────────────────────────────────────────

BEGIN;

-- ── Step 1: Rename patient_id -> masked_internal_id ──────────────────────────
ALTER TABLE biometric_registry
    RENAME COLUMN patient_id TO masked_internal_id;

-- ── Step 2: Rename bio_seed_hash -> bio_verifier_hash ────────────────────────
ALTER TABLE biometric_registry
    RENAME COLUMN bio_seed_hash TO bio_verifier_hash;

-- ── Step 3: Drop nfc_uid (PII — must not be stored) ─────────────────────────
-- This column stores a biometric device identifier in plaintext, which is
-- personal data under DPDP Act 2023 s.2(t).  The verifier already encodes
-- device identity via its HMAC input; retaining nfc_uid in the clear provides
-- no security benefit and creates unnecessary data-subject exposure.
ALTER TABLE biometric_registry
    DROP COLUMN nfc_uid;

-- ── Step 4: Add revoked_at ───────────────────────────────────────────────────
-- verify_biometric_binding() checks this on every handshake.
-- NULL = active binding.  Non-NULL = revoked; verify returns False regardless
-- of whether the verifier still matches.
ALTER TABLE biometric_registry
    ADD COLUMN revoked_at TIMESTAMPTZ NULL DEFAULT NULL;

-- ── Step 5: Add primary key ──────────────────────────────────────────────────
-- The original table had no PK.  We add a UUID column with a server-generated
-- default so existing rows are backfilled automatically.
ALTER TABLE biometric_registry
    ADD COLUMN id UUID NOT NULL DEFAULT gen_random_uuid();

ALTER TABLE biometric_registry
    ADD CONSTRAINT biometric_registry_pkey PRIMARY KEY (id);

-- ── Step 6: Add unique constraint (one binding per patient) ──────────────────
-- If this fails with "could not create unique index" there are duplicate
-- masked_internal_id values in the table.  Resolve them using the pre-flight
-- check queries above, then re-run this migration.
ALTER TABLE biometric_registry
    ADD CONSTRAINT uq_biometric_registry_patient UNIQUE (masked_internal_id);

-- ── Step 7: Index for handshake lookup performance ───────────────────────────
-- verify_biometric_binding() issues .eq("masked_internal_id", ...) on every
-- handshake.  The UNIQUE constraint above already creates a unique index on
-- this column, but naming it explicitly makes EXPLAIN output readable.
CREATE INDEX IF NOT EXISTS idx_biometric_registry_patient
    ON biometric_registry (masked_internal_id);

COMMIT;

-- ── Step 8: Flush PostgREST schema cache ─────────────────────────────────────
-- Run this AFTER the transaction commits.  It cannot be inside the BEGIN/COMMIT
-- block because pg_notify() takes effect on commit and cannot be rolled back.
--
-- This is required: without it PostgREST continues to serve the old cached
-- schema and you will still see PGRST204 ("column not found in schema cache")
-- errors even though the column now exists in Postgres.
--
-- In Supabase this is the canonical way to invalidate the PostgREST cache:
SELECT pg_notify('pgrst', 'reload schema');

-- ── Verification query ────────────────────────────────────────────────────────
-- Run after the migration to confirm the final schema matches expectations:
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name   = 'biometric_registry'
--   ORDER BY ordinal_position;
--
-- Expected output:
--   id                UUID   NO   gen_random_uuid()
--   masked_internal_id TEXT  NO   (null)
--   bio_verifier_hash  TEXT  NO   (null)
--   created_at         TSTZ  YES  now()
--   revoked_at         TSTZ  YES  (null)
--
-- Then re-run the smoke test:
--   BASE_URL=http://localhost:8000 CLINIC_API_KEY=... python3 scripts/smoke_test.py
--
-- Expected result: all checks PASS.