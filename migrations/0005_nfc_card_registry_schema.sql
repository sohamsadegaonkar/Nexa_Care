-- 0005_nfc_card_registry_schema.sql
--
-- Phase B: Identity Layer NFC card registry.
-- Creates the mapping table consumed by app/services/card_resolution_service.py.
--
-- PRIVACY NOTE
-- ------------
-- nfc_card_registry is strictly a card-state and masked-identity bridge. It
-- stores no patient name, DOB, phone, Aadhaar/ABHA value, diagnosis, lab, or
-- prescription data. The patient_id column is the masked internal UUID used by
-- authorized backend services to resolve the appropriate vault/clinical shard
-- path; no cross-shard foreign key is created here.
--
-- SECURITY NOTE
-- -------------
-- Resolution must fail closed in application code unless status = 'active'.
-- RLS is enabled so patient-facing authenticated roles cannot read or mutate
-- physical-card identity bindings. Backend service-role access remains
-- server-side only and must never be exposed to clients.

-- updated_at trigger (reuse if already present)
CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS nfc_card_registry (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    card_uid    VARCHAR(128) NOT NULL,
    patient_id  UUID         NOT NULL,
    status      VARCHAR(32)  NOT NULL DEFAULT 'active',
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    issued_by   UUID         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT uq_nfc_card_registry_card_uid UNIQUE (card_uid),
    CONSTRAINT ck_nfc_card_registry_status
        CHECK (status IN ('active', 'reported_lost', 'revoked', 'replaced'))
);

CREATE INDEX IF NOT EXISTS ix_nfc_card_registry_card_uid
    ON nfc_card_registry (card_uid);
CREATE INDEX IF NOT EXISTS ix_nfc_card_registry_patient_id
    ON nfc_card_registry (patient_id);
CREATE INDEX IF NOT EXISTS ix_nfc_card_registry_status
    ON nfc_card_registry (status);

DROP TRIGGER IF EXISTS trg_nfc_card_registry_updated_at ON nfc_card_registry;
CREATE TRIGGER trg_nfc_card_registry_updated_at
    BEFORE UPDATE ON nfc_card_registry
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

ALTER TABLE nfc_card_registry ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS nfc_card_registry_service_role_only ON nfc_card_registry;
CREATE POLICY nfc_card_registry_service_role_only
    ON nfc_card_registry
    FOR ALL
    TO authenticated
    USING (false);

SELECT pg_notify('pgrst', 'reload schema');
