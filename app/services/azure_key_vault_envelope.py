"""Opt-in shared Azure RSA wrapping for a fresh synthetic development database.

Does not provision or delete vault keys, import legacy ciphertext, or attest
irreversible erasure. The existing local provider's shared-key erasure path
truthfully caps assurance at access blocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timezone
from importlib import import_module

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConfigError, get_kms_config
from app.models.dek_store import PatientDEKStore
from app.services.crypto_kms import (
    DEKBundle,
    EncryptionError,
    LocalEnvelopeProvider,
    PatientDataErased,
    _acquire_dek_lifecycle_lock,
    _is_dek_destroyed,
)

_BACKEND = "azure-key-vault-rsa"
_ALGORITHM = "RSA-OAEP-256"


class AzureKeyVaultEnvelopeProvider(LocalEnvelopeProvider):
    """Azure Key Vault wraps each random DEK plus its patient/version binding."""

    def __init__(self) -> None:
        super().__init__()
        config = get_kms_config()
        if config.encryption_backend != "azure_key_vault":
            raise ConfigError("Explicit Azure Key Vault backend required")
        if not config.azure_key_id or not config.azure_identity_client_id:
            raise ConfigError("Incomplete Azure Key Vault configuration")
        self.key_id = config.azure_key_id
        self.identity_id = config.azure_identity_client_id
        try:
            self._credential_type = import_module("azure.identity").ManagedIdentityCredential
            self._crypto_type = import_module("azure.keyvault.keys.crypto").CryptographyClient
        except ImportError:
            raise ConfigError("Install the optional Azure SDK dependencies") from None

    def _binding(self, patient_id: str, version: int) -> bytes:
        return hashlib.sha256(
            b"nexa-azure-dek-v1\0"
            + uuid.UUID(patient_id).bytes
            + version.to_bytes(8, "big")
            + self.key_id.encode("ascii")
        ).digest()

    def _crypt(self, payload: bytes, *, unwrap: bool) -> bytes:
        try:
            with self._credential_type(client_id=self.identity_id) as credential:
                with self._crypto_type(
                    self.key_id, credential=credential, logging_enable=False,
                    retry_total=2, connection_timeout=10, read_timeout=20,
                ) as client:
                    if unwrap:
                        result = client.unwrap_key(
                            _ALGORITHM, payload, logging_enable=False
                        )
                        output = result.key
                    else:
                        result = client.wrap_key(
                            _ALGORITHM, payload, logging_enable=False
                        )
                        output = result.encrypted_key
                    if result.key_id != self.key_id or not isinstance(output, bytes):
                        raise EncryptionError("Unexpected Azure wrapping result")
                    return output
        except Exception:  # noqa: BLE001 - provider messages can leak sensitive context
            raise EncryptionError("Azure Key Vault operation failed") from None

    def _validate_row(self, row: PatientDEKStore) -> None:
        if (
            row.wrapping_backend != _BACKEND
            or row.wrapping_key_type != "shared"
            or row.patient_wrapping_key_id != self.key_id
            or row.dek_iv != b""
            or row.algorithm != "AES-256-GCM"
        ):
            raise EncryptionError("Incompatible Azure DEK metadata")

    async def _get_plaintext_dek(
        self, patient_id: str, version: int, db: AsyncSession
    ) -> bytes:
        await self._check_erasure_registry(patient_id, db)
        result = await db.execute(
            select(PatientDEKStore).where(and_(
                PatientDEKStore.patient_id == uuid.UUID(patient_id),
                PatientDEKStore.dek_version == version,
            ))
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise EncryptionError("DEK unavailable")
        if _is_dek_destroyed(row):
            raise PatientDataErased(patient_id)
        self._validate_row(row)
        # No plaintext DEK cache: disabled/unavailable vault keys fail closed.
        plain = await asyncio.to_thread(self._crypt, row.wrapped_dek, unwrap=True)
        if len(plain) != 64 or not hmac.compare_digest(
            plain[:32], self._binding(patient_id, version)
        ):
            raise EncryptionError("Azure DEK binding mismatch")
        return plain[32:]

    async def _rows(self, patient_id: str, db: AsyncSession) -> list[PatientDEKStore]:
        result = await db.execute(
            select(PatientDEKStore).where(
                PatientDEKStore.patient_id == uuid.UUID(patient_id)
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            self._validate_row(row)
        return rows

    @staticmethod
    def _bundle(row: PatientDEKStore) -> DEKBundle:
        return DEKBundle(
            patient_id=str(row.patient_id), wrapped_dek=row.wrapped_dek,
            dek_version=row.dek_version, algorithm=row.algorithm,
            created_at=row.created_at,
        )

    async def _new_row(
        self, patient_id: str, version: int, db: AsyncSession
    ) -> PatientDEKStore:
        payload = self._binding(patient_id, version) + os.urandom(32)
        wrapped = await asyncio.to_thread(self._crypt, payload, unwrap=False)
        if not 256 <= len(wrapped) <= 512:
            raise EncryptionError("Unexpected Azure wrapped DEK size")
        row = PatientDEKStore(
            patient_id=uuid.UUID(patient_id), wrapped_dek=wrapped, dek_iv=b"",
            dek_version=version, algorithm="AES-256-GCM", wrapping_backend=_BACKEND,
            wrapping_key_type="shared", patient_wrapping_key_id=self.key_id,
            is_active=True, created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.flush()
        return row

    async def _active_rows(
        self, patient_id: str, db: AsyncSession
    ) -> list[PatientDEKStore]:
        await self._check_erasure_registry(patient_id, db)
        await _acquire_dek_lifecycle_lock(db, patient_id)
        # Recheck erasure after waiting for the shared lifecycle lock.
        await self._check_erasure_registry(patient_id, db)
        rows = await self._rows(patient_id, db)
        if any(_is_dek_destroyed(row) for row in rows):
            raise PatientDataErased(patient_id)
        return rows

    async def ensure_active_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        rows = await self._active_rows(patient_id, db)
        active = [row for row in rows if row.is_active]
        if len(active) == 1:
            return self._bundle(active[0])
        if rows:
            raise EncryptionError("Invalid Azure DEK lifecycle state")
        return self._bundle(await self._new_row(patient_id, 1, db))

    async def generate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        if await self._active_rows(patient_id, db):
            raise EncryptionError("Azure DEK already exists")
        result = self._bundle(await self._new_row(patient_id, 1, db))
        await db.commit()
        return result

    async def rotate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        rows = await self._active_rows(patient_id, db)
        if sum(bool(row.is_active) for row in rows) != 1:
            raise EncryptionError("Invalid Azure DEK lifecycle state")
        version = max(row.dek_version for row in rows) + 1
        new_row = await self._new_row(patient_id, version, db)
        for row in rows:
            row.is_active = False
        await db.commit()
        return self._bundle(new_row)

    async def destroy_dek(self, patient_id: str, db: AsyncSession) -> bool:
        # No mixed legacy row may be classified as Azure shared-key erasure.
        await _acquire_dek_lifecycle_lock(db, patient_id)
        await self._rows(patient_id, db)
        return await super().destroy_dek(patient_id, db)
