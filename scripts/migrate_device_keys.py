"""Batch migration script: Plaintext device keys to Envelope Encryption."""

from __future__ import annotations

import asyncio
import base64
import logging
import sys
from pathlib import Path

# Add root to path before app imports to satisfy E402
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import get_session_factory # noqa: E402
from app.services.crypto_kms import get_encryption_provider, EncryptedField # noqa: E402
from app.core.supabase import get_supabase_client # noqa: E402
from app.observability.audit_ledger import append_audit_log # noqa: E402

logger = logging.getLogger("nexa_logger")

async def migrate_row(row: dict, session, kms) -> bool:
    """Migrate a single row's device_public_key."""
    patient_id = row["masked_internal_id"]
    raw_key = row["device_public_key"]
    
    if not raw_key:
        return False
        
    try:
        # Check if already migrated
        try:
            EncryptedField.deserialize(raw_key, "device_public_key")
            return False # Already migrated
        except Exception:
            pass # Not migrated

        # 1. Parse raw key
        if isinstance(raw_key, str):
            if raw_key.startswith("\\x"):
                raw_key = bytes.fromhex(raw_key[2:])
            else:
                try:
                    raw_key = base64.b64decode(raw_key)
                except Exception:
                    raw_key = raw_key.encode("utf-8") # Fallback

        # 2. Re-encrypt with KMS
        plaintext_key_b64 = base64.b64encode(raw_key).decode("utf-8")
        new_encrypted = await kms.encrypt_field(patient_id, "device_public_key", plaintext_key_b64, session)
        serialized = new_encrypted.serialize()

        # 3. Update Supabase
        supabase = get_supabase_client()
        supabase.table("biometric_registry").update(
            {"device_public_key": serialized}
        ).eq("id", row["id"]).execute()
        
        await append_audit_log(
            actor_uid="SYSTEM_MIGRATION_BATCH",
            event_type="DEVICE_KEY_ENCRYPTION_MIGRATED",
            target_id=patient_id,
            status="SUCCESS",
            metadata={"field": "device_public_key", "mechanism": "batch_migration"}
        )
        return True
            
    except Exception as exc:
        logger.error(f"Failed to migrate patient {patient_id}: {exc}")
        
    return False

async def main():
    kms = get_encryption_provider()
    supabase = get_supabase_client()
    
    print("Starting device_public_key encryption migration")
    
    # Fetch all rows
    response = supabase.table("biometric_registry").select("*").execute()
    rows = getattr(response, "data", [])
    
    total = len(rows)
    migrated = 0
    skipped = 0
    
    session_factory = get_session_factory()
    async with session_factory() as session:
        for i, row in enumerate(rows):
            was_migrated = await migrate_row(row, session, kms)
            if was_migrated:
                migrated += 1
            else:
                skipped += 1
                
            if (i + 1) % 10 == 0:
                print(f"Progress: {i+1}/{total}...")
                await session.commit()
                
        await session.commit()
        
    print(f"Migration complete. Total: {total}, Migrated: {migrated}, Skipped: {skipped}")

if __name__ == "__main__":
    asyncio.run(main())
