"""Splits OCR/extraction model output into the PII vault shard and the
"de-identified" clinical shard.
"""
from __future__ import annotations

import json
import logging

from app.core.security import decrypt_pii_field
from app.observability.redactor import SENSITIVE_FIELDS as PII_FIELD_NAMES
from app.services.crypto_kms import get_encryption_provider, EncryptedField, LegacyFernetError, EncryptionProvider
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from app.models.shards import NexaVault

logger = logging.getLogger("nexa_logger")

# Fields known to be safe to file under the "anonymized" clinical shard.
CLINICAL_FIELD_NAMES = {"diagnoses", "lab_results", "prescriptions"}


# Columns that replace the legacy raw_pii JSONB blob in nexa_vault.
_VAULT_PII_COLUMNS = {"patient_name", "phone", "aadhaar_abha_id"}


async def encrypt_vault_payload(
    vault_payload: dict,
    patient_id: str,
    db: AsyncSession,
    provider: EncryptionProvider | None = None
) -> dict[str, str | None]:
    """Map a PII vault payload to the encrypted column set using KMS.

    Security Sprint (Sprint 2): Every PII field is now encrypted with a
    per-patient DEK instead of a global static key.
    """
    kms = provider or get_encryption_provider()
    encrypted_payload = {}
    for column in _VAULT_PII_COLUMNS:
        plaintext = vault_payload.get(column)
        if plaintext:
            encrypted_field = await kms.encrypt_field(patient_id, column, plaintext, db)
            encrypted_payload[column] = encrypted_field.serialize()
        else:
            encrypted_payload[column] = None
    return encrypted_payload


async def decrypt_vault_field(
    patient_id: str,
    field_name: str,
    serialized_data: str | None,
    db: AsyncSession,
    provider: EncryptionProvider | None = None
) -> str | None:
    """Decrypt a single vault field with transparent auto-migration from Fernet."""
    if not serialized_data:
        return None

    kms = provider or get_encryption_provider()
    try:
        encrypted_field = EncryptedField.deserialize(serialized_data, field_name)
        return await kms.decrypt_field(patient_id, field_name, encrypted_field, db)
    except LegacyFernetError as legacy:
        # 1. Decrypt with old Fernet key
        plaintext = decrypt_pii_field(legacy.data)
        if not plaintext:
            return None

        # 2. Re-encrypt with per-patient DEK
        # This is the "auto-migration on first read" (Sprint 2 Requirement)
        logger.info(json.dumps({
            "event": "pii_auto_migration_on_read",
            "patient_id": patient_id,
            "field": field_name
        }))
        
        try:
            new_encrypted = await kms.encrypt_field(patient_id, field_name, plaintext, db)
            serialized = new_encrypted.serialize()
            
            # 3. Write back to DB
            stmt = (
                update(NexaVault)
                .where(NexaVault.masked_internal_id == patient_id)
                .values({field_name: serialized})
            )
            await db.execute(stmt)
            
            from app.observability.audit_ledger import append_audit_log
            await append_audit_log(
                actor_uid="SYSTEM_AUTO_MIGRATE",
                event_type="PII_ENCRYPTION_MIGRATED",
                target_id=patient_id,
                status="SUCCESS",
                metadata={"field": field_name, "mechanism": "read_trigger"}
            )
        except Exception as exc:
            logger.error(f"Failed to write back auto-migrated field {field_name} for {patient_id}: {exc}")
            
        return plaintext


def split_pii_and_clinical_fields(
    extracted: dict,
) -> tuple[dict, dict, dict]:
    """Splits `extracted` (raw OCR output) into three dicts:
    (vault_payload, clinical_payload, unrecognized_payload).
    """
    vault_payload: dict = {}
    clinical_payload: dict = {}
    unrecognized_payload: dict = {}

    for key, value in extracted.items():
        normalized_key = key.lower() if isinstance(key, str) else key

        if normalized_key in PII_FIELD_NAMES:
            vault_payload[key] = value
        elif normalized_key in CLINICAL_FIELD_NAMES:
            clinical_payload[key] = value
        else:
            unrecognized_payload[key] = value

    return vault_payload, clinical_payload, unrecognized_payload
