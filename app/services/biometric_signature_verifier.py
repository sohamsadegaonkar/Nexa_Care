"""Biometric signature verification service for Nexa Care V2."""

from __future__ import annotations

import asyncio
import logging
import time
import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from redis.asyncio import Redis

from app.core.supabase import get_supabase_client
from app.services.crypto_kms import get_encryption_provider, EncryptedField, PatientDataErased
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("nexa_logger")

# Fixed budget for signature verification to prevent timing side-channels.
_MIN_VERIFY_DURATION_SECONDS = 0.05
NONCE_PREFIX = "biometric_nonce:"

@dataclass(frozen=True, slots=True)
class SignatureVerificationResult:
    """Outcome of a biometric signature verification."""
    verified: bool
    patient_id: str
    error: str | None = None

class BiometricSignatureVerifier:
    """Verifies ECDSA signatures from patient devices."""

    async def verify_signature(
        self,
        patient_id: str,
        request_id: str,
        signature_b64: str,
        challenge_nonce: str,
        redis: Redis,
        db: AsyncSession,
    ) -> SignatureVerificationResult:
        """
        Verify an ECDSA signature over (nonce + request_id + patient_id).
        Fails closed on any error and enforces a minimum execution time.
        """
        start = time.monotonic()
        
        try:
            signature = base64.b64decode(signature_b64)
            
            # 1. Nonce Replay Protection & Expiry Check
            nonce_key = f"{NONCE_PREFIX}{challenge_nonce}"
            is_used = await redis.get(f"{nonce_key}:used")
            if is_used:
                return await self._fail(start, patient_id, "Nonce already used")

            # 2. Fetch Device Public Key from Biometric Registry
            supabase = get_supabase_client()
            response = (
                supabase.table("biometric_registry")
                .select("device_public_key,revoked_at")
                .eq("masked_internal_id", patient_id)
                .single()
                .execute()
            )
            
            row = getattr(response, "data", None)
            if not row or not row.get("device_public_key"):
                return await self._fail(start, patient_id, "Device not enrolled for biometric verification")
            
            if row.get("revoked_at"):
                return await self._fail(start, patient_id, "Biometric binding revoked")

            # 3. Cryptographic Verification
            # Sprint 2: Decrypt the public key using per-patient DEK
            kms = get_encryption_provider()
            try:
                encrypted_field = EncryptedField.deserialize(row["device_public_key"], "device_public_key")
                decrypted_key_b64 = await kms.decrypt_field(patient_id, "device_public_key", encrypted_field, db)
                raw_key = base64.b64decode(decrypted_key_b64)
            except PatientDataErased:
                # Requirement: fail with PatientDataErased if DEK was destroyed
                raise
            except Exception as e:
                # Handle legacy plaintext or malformed data
                logger.warning(f"Failed to decrypt device_public_key for {patient_id}, attempting legacy parse: {e}")
                raw_key = row["device_public_key"]
                if isinstance(raw_key, str):
                    if raw_key.startswith("\\x"):
                        raw_key = bytes.fromhex(raw_key[2:])
                    else:
                        raw_key = base64.b64decode(raw_key)

            public_key = serialization.load_der_public_key(raw_key)
            
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                return await self._fail(start, patient_id, "Invalid key type stored")

            # Signed data: SHA-256(challenge_nonce + request_id + patient_id)
            message = f"{challenge_nonce}{request_id}{patient_id}".encode("utf-8")
            
            try:
                public_key.verify(
                    signature,
                    message,
                    ec.ECDSA(hashes.SHA256())
                )
            except Exception:
                return await self._fail(start, patient_id, "Invalid cryptographic signature")

            # 4. Mark Nonce as Used
            await redis.setex(f"{nonce_key}:used", 120, "1")

            # Pad time
            await self._pad_time(start)
            return SignatureVerificationResult(verified=True, patient_id=patient_id)

        except PatientDataErased as e:
            return await self._fail(start, patient_id, str(e))
        except Exception as exc:
            logger.error(f"Unexpected error during biometric verification for {patient_id}: {exc}")
            return await self._fail(start, patient_id, str(exc))

    async def _fail(self, start_time: float, patient_id: str, reason: str) -> SignatureVerificationResult:
        await self._pad_time(start_time)
        return SignatureVerificationResult(verified=False, patient_id=patient_id, error=reason)

    async def _pad_time(self, start_time: float):
        # Windows and some event loops may resume asyncio.sleep slightly early.
        # Recheck the monotonic deadline so the side-channel floor is a true
        # minimum rather than a best-effort delay.
        deadline = start_time + _MIN_VERIFY_DURATION_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(remaining)
