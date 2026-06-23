-- 0005_nfc_card_registry_schema.sql
--
-- Phase B: NFC Identity Bridge — card lifecycle registry and immutable event ledger.
--
-- PRIVACY NOTE
-- ------------
-- nfc_card_registry stores card UIDs and patient linkage only. Clinical data
-- and PII remain in their respective shards; resolve returns patient_id for
-- ACTIVE cards only.
--
-- PREREQUISITE
-- ------------
-- nexa_vault must exist with an ``id`` UUID primary key before applying the
-- patient_id foreign key below.
--
-- Apply against your Supabase/Postgres instance (SQL editor or migration tool).

-- ── nfc_card_registry ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS nfc_card_registry (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    card_uid          VARCHAR(128) NOT NULL,
    patient_id        UUID        NULL REFERENCES nexa_vault(id) ON DELETE CASCADE,
    status            VARCHAR(32) NOT NULL,
    source_type       VARCHAR(16) NOT NULL,
    hospital_id       UUID        NULL REFERENCES hospital_registry(id) ON DELETE SET NULL,
    card_type         VARCHAR(32) NOT NULL DEFAULT 'PATIENT_CARD',
    activated_at      TIMESTAMPTZ NULL,
    activated_by      UUID        NULL,
    previous_card_uid VARCHAR(128) NULL,
    replaced_by_uid   VARCHAR(128) NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_nfc_card_registry_card_uid UNIQUE (card_uid)
);

CREATE INDEX IF NOT EXISTS ix_nfc_card_registry_card_uid
    ON nfc_card_registry (card_uid);
CREATE INDEX IF NOT EXISTS ix_nfc_card_registry_status
    ON nfc_card_registry (status);
CREATE INDEX IF NOT EXISTS ix_nfc_card_registry_patient_id
    ON nfc_card_registry (patient_id);
CREATE INDEX IF NOT EXISTS ix_nfc_card_registry_hospital_id
    ON nfc_card_registry (hospital_id);

DROP TRIGGER IF EXISTS trg_nfc_card_registry_updated_at ON nfc_card_registry;
CREATE TRIGGER trg_nfc_card_registry_updated_at
    BEFORE UPDATE ON nfc_card_registry
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- ── nfc_card_event (immutable ledger) ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS nfc_card_event (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id     UUID        NOT NULL REFERENCES nfc_card_registry(id) ON DELETE CASCADE,
    event_type  VARCHAR(32) NOT NULL,
    actor_uid   UUID        NULL,
    details     JSONB       NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_nfc_card_event_card_id
    ON nfc_card_event (card_id);
CREATE INDEX IF NOT EXISTS ix_nfc_card_event_event_type
    ON nfc_card_event (event_type);
CREATE INDEX IF NOT EXISTS ix_nfc_card_event_created_at
    ON nfc_card_event (created_at);

-- Reload PostgREST schema cache when applicable
SELECT pg_notify('pgrst', 'reload schema');
