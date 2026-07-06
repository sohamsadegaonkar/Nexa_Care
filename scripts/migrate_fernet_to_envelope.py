"""Batch migration script: Fernet to Envelope Encryption for Identity Vault."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add root to path before app imports to satisfy E402
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlalchemy # noqa: E402
from app.core.database import get_session_factory # noqa: E402
from app.models.shards import NexaVault # noqa: E402
from app.services.crypto_kms import get_encryption_provider, LegacyFernetError, EncryptedField # noqa: E402
from app.core.security import decrypt_pii_field # noqa: E402
from app.observability.audit_ledger import append_audit_log # noqa: E402

logger = logging.getLogger("nexa_logger")

async def migrate_row(row: NexaVault, session, kms) -> bool:
    """Migrate a single row from global Fernet to per-patient envelope encryption."""
    patient_id = row.masked_internal_id
    updated_fields = {}
    
    fields = ["patient_name", "phone", "aadhaar_abha_id"]
    
    try:
        for field in fields:
            val = getattr(row, field)
            if not val:
                continue
                
            try:
                # Check if already migrated
                EncryptedField.deserialize(val, field)
            except LegacyFernetError as legacy:
                # 1. Decrypt with old Fernet
                plaintext = decrypt_pii_field(legacy.data)
                if plaintext:
                    # 2. Re-encrypt with KMS
                    new_encrypted = await kms.encrypt_field(patient_id, field, plaintext, session)
                    updated_fields[field] = new_encrypted.serialize()
            except Exception:
                # Probably malformed or other error, skip this field
                continue

        if updated_fields:
            stmt = (
                sqlalchemy.update(NexaVault)
                .where(NexaVault.masked_internal_id == patient_id)
                .values(updated_fields)
            )
            await session.execute(stmt)
            
            await append_audit_log(
                actor_uid="SYSTEM_MIGRATION_BATCH",
                event_type="PII_ENCRYPTION_MIGRATED",
                target_id=patient_id,
                status="SUCCESS",
                metadata={"fields": list(updated_fields.keys()), "mechanism": "batch_migration"}
            )
            return True
            
    except Exception as exc:
        logger.error(f"Failed to migrate patient {patient_id}: {exc}")
        
    return False

async def main():
    kms = get_encryption_provider()
    
    print("Starting PII encryption migration: Fernet -> Envelope")
    
    session_factory = get_session_factory()
    async with session_factory() as session:
        # Fetch all patients in the vault
        stmt = sqlalchemy.select(NexaVault)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        total = len(rows)
        migrated = 0
        skipped = 0
        
        for i, row in enumerate(rows):
            was_migrated = await migrate_row(row, session, kms)
            if was_migrated:
                migrated += 1
            else:
                skipped += 1
                
            if (i + 1) % 10 == 0:
                print(f"Progress: {i+1}/{total}...")
                await session.commit() # Intermittent commits
                
        await session.commit()
        
    print(f"Migration complete. Total: {total}, Migrated: {migrated}, Skipped/Already Migrated: {skipped}")

if __name__ == "__main__":
    asyncio.run(main())
