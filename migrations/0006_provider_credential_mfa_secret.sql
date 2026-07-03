-- 0006_provider_credential_mfa_secret.sql
--
-- Adds the mfa_secret column used by the TOTP implementation in
-- app/services/provider_auth_service.py. The encrypted sibling
-- (mfa_secret_encrypted) remains reserved for a future at-rest encryption
-- pass; mfa_secret is treated as a sensitive credential and is never
-- returned to clients.

ALTER TABLE provider_credential
ADD COLUMN IF NOT EXISTS mfa_secret TEXT NULL;
