-- Nexa Care v1.0 Core Schema
-- Identity Vault + Consent Ledger + Merge Support
-- PostgreSQL 15+

-- ============================================
-- 1. PATIENTS (Core Identity Vault)
-- ============================================
CREATE TABLE patients (
    patient_uuid          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- PII fields (encrypted at rest in production)
    full_name             TEXT,
    date_of_birth         DATE,
    gender                TEXT,
    phone                 TEXT,
    email                 TEXT,
    abha_id               TEXT UNIQUE,
    address_line1         TEXT,
    address_line2         TEXT,
    city                  TEXT,
    state                 TEXT,
    pincode               TEXT,
    emergency_contact_name TEXT,
    emergency_contact_phone TEXT,
    -- Consent Assurance Policy (patient-owned)
    consent_assurance_policy TEXT NOT NULL DEFAULT 'STANDARD'
        CHECK (consent_assurance_policy IN ('STANDARD', 'PUSH_APPROVAL', 'BIOMETRIC')),
    -- Cryptographic erasure support
    dek_id                TEXT,                    -- Data Encryption Key reference (HSM)
    is_deleted            BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_patients_abha ON patients (abha_id) WHERE abha_id IS NOT NULL;
CREATE INDEX idx_patients_phone ON patients (phone) WHERE phone IS NOT NULL;

-- ============================================
-- 2. PATIENT EXTERNAL IDENTIFIERS
-- ============================================
CREATE TABLE patient_external_ids (
    id                    BIGSERIAL PRIMARY KEY,
    patient_uuid          UUID NOT NULL REFERENCES patients(patient_uuid) ON DELETE CASCADE,
    id_type               TEXT NOT NULL,           -- 'ABHA', 'AADHAAR', 'PAN', 'PASSPORT', etc.
    id_value              TEXT NOT NULL,
    verified              BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_type, id_value)
);

-- ============================================
-- 3. CARD REGISTRY (NFC Authentication Tokens)
-- ============================================
CREATE TABLE card_registry (
    card_id               TEXT PRIMARY KEY,        -- Random public card identifier
    patient_uuid          UUID NOT NULL REFERENCES patients(patient_uuid),
    card_version          INTEGER NOT NULL DEFAULT 1,
    status                TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'REVOKED', 'LOST')),
    issued_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at            TIMESTAMPTZ,
    last_used_at          TIMESTAMPTZ
);

CREATE INDEX idx_card_registry_patient ON card_registry(patient_uuid);

-- ============================================
-- 4. CONSENT LEDGER (Immutable Append-Only)
-- ============================================
CREATE TABLE consent_ledger (
    consent_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_uuid          UUID NOT NULL REFERENCES patients(patient_uuid),
    hospital_id           TEXT NOT NULL,
    clinician_id          TEXT NOT NULL,
    purpose               TEXT NOT NULL,
    consent_assurance     TEXT NOT NULL,           -- 'standard', 'push_approved', 'biometric_confirmed', 'bypassed_emergency'
    granted_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at            TIMESTAMPTZ,
    expires_at            TIMESTAMPTZ,
    digital_signature     TEXT,
    policy_change_direction TEXT,                  -- 'upgrade' | 'downgrade' (nullable)
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_consent_ledger_patient ON consent_ledger(patient_uuid);
CREATE INDEX idx_consent_ledger_active ON consent_ledger(patient_uuid, revoked_at) WHERE revoked_at IS NULL;

-- ============================================
-- 5. AUDIT LEDGER (Immutable)
-- ============================================
CREATE TABLE audit_ledger (
    audit_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_uuid          UUID,
    actor_type            TEXT NOT NULL,           -- 'provider', 'patient', 'system'
    actor_id              TEXT NOT NULL,
    action                TEXT NOT NULL,           -- 'login', 'nfc_tap', 'record_view', 'break_glass', 'consent_issue'
    resource              TEXT,
    details               JSONB,
    ip_address            INET,
    user_agent            TEXT,
    timestamp             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_ledger_patient ON audit_ledger(patient_uuid);
CREATE INDEX idx_audit_ledger_timestamp ON audit_ledger(timestamp);

-- ============================================
-- 6. PATIENT TOMBSTONES (Merge / Alias Support)
-- ============================================
CREATE TABLE patient_tombstones (
    tombstone_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    old_patient_uuid      UUID NOT NULL,
    canonical_patient_uuid UUID NOT NULL REFERENCES patients(patient_uuid),
    merged_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    merged_by             TEXT NOT NULL,           -- Clinical_Admin or Data_Steward
    reason                TEXT,
    evidence              JSONB
);

CREATE INDEX idx_tombstones_old ON patient_tombstones(old_patient_uuid);
CREATE INDEX idx_tombstones_canonical ON patient_tombstones(canonical_patient_uuid);

-- ============================================
-- 7. CONSENT SESSIONS (Runtime)
-- ============================================
CREATE TABLE consent_sessions (
    session_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_uuid          UUID NOT NULL REFERENCES patients(patient_uuid),
    consent_token         TEXT NOT NULL UNIQUE,
    purpose               TEXT NOT NULL,
    consent_assurance     TEXT NOT NULL,
    issued_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at            TIMESTAMPTZ NOT NULL,
    revoked_at            TIMESTAMPTZ,
    hospital_id           TEXT,
    clinician_id          TEXT
);

CREATE INDEX idx_consent_sessions_token ON consent_sessions(consent_token);
CREATE INDEX idx_consent_sessions_patient ON consent_sessions(patient_uuid);