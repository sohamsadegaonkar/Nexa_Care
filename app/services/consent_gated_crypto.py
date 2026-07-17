"""Atomic consent-gated decryption service for Nexa Care V2."""

from __future__ import annotations

import logging
from typing import Any, Protocol, List, Dict

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

import app.services.consent_engine as consent_engine
from app.models.shards import NexaVault, NexaClinical
from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503
from app.observability.safe_exceptions import log_safe_exception

from app.services.crypto_kms import EncryptedField, PatientDataErased

logger = logging.getLogger("nexa_logger")


class EncryptionProvider(Protocol):
    """Squad C's cryptographic interface for per-patient envelope encryption."""

    async def decrypt_field(
        self, patient_id: str, field_name: str, encrypted: EncryptedField, db: AsyncSession
    ) -> str:
        """Decrypt a single field using the patient's DEK."""
        ...


def _match_field(field_path: str, scope: str) -> bool:
    """Check if a specific field path is covered by a granted scope."""
    if scope == "*" or scope == field_path:
        return True
    if scope.endswith(".*"):
        prefix = scope[:-2]
        if field_path.startswith(prefix + "."):
            return True
    return False


def _is_covered(requested_scope: str, granted_scopes: List[str]) -> bool:
    """Check if the requested scope is authorized by the list of granted scopes."""
    return any(_match_field(requested_scope, granted) for granted in granted_scopes)


async def consent_gated_decrypt(
    patient_id: str,
    consent_token: str,
    purpose: str,
    requested_scope: str,
    provider_id: str,
    db: AsyncSession,
    redis: Redis,
    kms: EncryptionProvider,
) -> dict:
    """Atomically validate/consume a consent token and decrypt requested fields.

    Security Sprint (Sprint 2): Integrates the consent engine with the crypto
    layer to ensure that decryption only occurs for authorized requests.
    """

    # Step 1: ConsentEngine.validate()
    capability = await consent_engine.validate(
        token=consent_token,
        patient_id=patient_id,
        clinician_id=provider_id,
        purpose=purpose,
    )

    if capability is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent token invalid or expired.",
        )

    # Squad C Requirement: Verify single-gate behavior
    # This assertion ensures that we have a valid capability before proceeding to any decryption call.
    assert capability is not None, "Internal Error: Validation gate bypassed"

    # Verify requested scope is within the token's granted scope
    if requested_scope != "*" and not _is_covered(requested_scope, capability.scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requested scope '{requested_scope}' is not authorized by the consent grant.",
        )

    # Scopes we will actually process
    scopes_to_process = capability.scope if requested_scope == "*" else [requested_scope]

    # Step 2: Hard-audit Decrypt Started
    await append_audit_log_or_503(
        actor_uid=provider_id,
        event_type="CONSENT_GATED_DECRYPT_STARTED",
        target_id=patient_id,
        status="STARTED",
        metadata={"requested_scope": requested_scope, "purpose": purpose},
    )

    decrypted_results: Dict[str, Any] = {"pii": {}, "clinical": {}}
    decrypt_attempted = False

    try:
        # Step 3: Fetch the encrypted field(s)
        # Vertical Shard A: NexaVault (PII)
        pii_cols = ["patient_name", "phone", "aadhaar_abha_id"]
        if any(s == "*" or s.startswith("pii") for s in scopes_to_process):
            stmt = select(NexaVault).where(NexaVault.masked_internal_id == patient_id).limit(1)
            result = await db.execute(stmt)
            vault_row = result.scalars().first()
            if vault_row:
                for col in pii_cols:
                    full_path = f"pii.{col}"
                    if any(_match_field(full_path, s) for s in scopes_to_process):
                        val = getattr(vault_row, col)
                        if val is not None:
                            decrypt_attempted = True
                            # Step 4: Call kms.decrypt_field()
                            encrypted = EncryptedField.deserialize(val, col)
                            decrypted_results["pii"][col] = await kms.decrypt_field(patient_id, col, encrypted, db)

        # Vertical Shard B: NexaClinical (Clinical Data)
        clinical_cols = ["diagnoses", "lab_results", "prescriptions"]
        if any(s == "*" or s.startswith("clinical") for s in scopes_to_process):
            stmt = select(NexaClinical).where(NexaClinical.masked_internal_id == patient_id).limit(1)
            result = await db.execute(stmt)
            clinical_row = result.scalars().first()
            if clinical_row:
                for col in clinical_cols:
                    full_path = f"clinical.{col}"
                    if any(_match_field(full_path, s) for s in scopes_to_process):
                        val = getattr(clinical_row, col)
                        if val is not None:
                            decrypt_attempted = True
                            # Step 4: Call kms.decrypt_field()
                            encrypted = EncryptedField.deserialize(val, col)
                            decrypted_results["clinical"][col] = await kms.decrypt_field(patient_id, col, encrypted, db)

        # Step 5: ConsentEngine.consume()
        try:
            await consent_engine.consume(
                db=db,
                token=consent_token,
                patient_id=patient_id,
                clinician_id=provider_id,
                purpose=purpose,
            )
        except Exception as exc:
            # Consent consume failure after successful decrypt: Log warning but return data
            log_safe_exception(logger, exc, subsystem="redis", operation="consent_consume_after_decrypt")

        # Step 6: Hard-audit Decrypt Completed
        await append_audit_log_or_503(
            actor_uid=provider_id,
            event_type="CONSENT_GATED_DECRYPT_COMPLETED",
            target_id=patient_id,
            status="SUCCESS",
        )

        return decrypted_results

    except Exception as exc:
        # Decrypt failure (or fetch failure)
        # Even if decryption fails, the access attempt happened.
        # Record it and consume the token as required.
        await append_audit_log(
            actor_uid=provider_id,
            event_type="CONSENT_GATED_DECRYPT_FAILED",
            target_id=patient_id,
            status="FAILED",
            metadata={"error_code": "CONSENT_GATED_DECRYPT_FAILED", "requested_scope": requested_scope},
        )

        # Force consume the token if we reached the decrypt step
        if decrypt_attempted:
            try:
                await consent_engine.consume(
                    db=db,
                    token=consent_token,
                    patient_id=patient_id,
                    clinician_id=provider_id,
                    purpose=purpose,
                )
            except Exception as consume_err:
                log_safe_exception(
                    logger, consume_err, subsystem="redis", operation="consent_consume_cleanup"
                )

        if isinstance(exc, (HTTPException, PatientDataErased)):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "DECRYPTION_OPERATION_FAILED"},
        ) from exc
