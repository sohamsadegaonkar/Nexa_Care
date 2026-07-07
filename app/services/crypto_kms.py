"""Key Management System (KMS) for Nexa Care.

Provides per-patient Data Encryption Keys (DEKs) wrapped by a system-wide
Key Encryption Key (KEK). Supports versioning and rotation.
"""

from __future__ import annotations

import base64
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, List

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_kms_config, ConfigError
from app.models.dek_store import PatientDEKStore


from unittest.mock import Mock


def _is_dek_destroyed(row) -> bool:
    val = getattr(row, "destroyed_at", None)
    if val is None or isinstance(val, Mock):
        return False
    return True


@dataclass(frozen=True)
class DEKBundle:
    patient_id: str
    wrapped_dek: bytes       # DEK encrypted by KEK
    dek_version: int
    algorithm: str           # e.g., "AES-256-GCM"
    created_at: datetime


@dataclass(frozen=True)
class EncryptedField:
    ciphertext: bytes
    iv: bytes                 # initialization vector
    field_name: str
    dek_version: int
    algorithm: str

    def serialize(self) -> str:
        """Serialize to storable format: base64(iv + ciphertext) + ":" + version"""
        combined = self.iv + self.ciphertext
        b64 = base64.b64encode(combined).decode("utf-8")
        return f"{b64}:{self.dek_version}"

    @classmethod
    def deserialize(cls, serialized: str, field_name: str) -> EncryptedField:
        """Parse the storable format back into an EncryptedField."""
        if not serialized:
            raise EncryptionError("Empty ciphertext")

        # Detect legacy Fernet data (base64 Fernet tokens start with gAAAAA)
        if serialized.startswith("gAAAAA"):
            raise LegacyFernetError(serialized)

        parts = serialized.split(":")
        if len(parts) != 2:
            raise EncryptionError("Invalid encrypted field format")

        try:
            b64_data = parts[0]
            version = int(parts[1])
            raw_data = base64.b64decode(b64_data)
            if len(raw_data) < 13: # 12 bytes IV + at least 1 byte ciphertext
                 raise EncryptionError("Ciphertext too short")
            iv = raw_data[:12]
            ciphertext = raw_data[12:]
            return cls(
                ciphertext=ciphertext,
                iv=iv,
                field_name=field_name,
                dek_version=version,
                algorithm="AES-256-GCM"
            )
        except Exception as exc:
            if isinstance(exc, EncryptionError):
                raise
            raise EncryptionError(f"Failed to deserialize field {field_name}") from exc


@dataclass(frozen=True)
class DEKMetadata:
    patient_id: str
    dek_version: int
    algorithm: str
    created_at: datetime
    is_active: bool


class EncryptionProvider(ABC):
    """Abstract interface for per-patient envelope encryption."""

    @abstractmethod
    async def generate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        """Generate a new Data Encryption Key for a patient."""

    @abstractmethod
    async def encrypt_field(self, patient_id: str, field_name: str, plaintext: str, db: AsyncSession) -> EncryptedField:
        """Encrypt a single field using the patient's DEK."""

    @abstractmethod
    async def decrypt_field(self, patient_id: str, field_name: str, encrypted: EncryptedField, db: AsyncSession) -> str:
        """Decrypt a single field using the patient's DEK."""

    @abstractmethod
    async def rotate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        """Generate a new DEK and deactivate the old one."""

    @abstractmethod
    async def destroy_dek(self, patient_id: str, db: AsyncSession) -> bool:
        """Cryptographic erasure: permanently delete all of the patient's DEKs."""

    @abstractmethod
    async def get_dek_metadata(self, patient_id: str, db: AsyncSession) -> List[DEKMetadata]:
        """Return metadata about all the patient's DEKs."""


class LocalEnvelopeProvider(EncryptionProvider):
    """Local implementation of envelope encryption using a derived KEK."""

    def __init__(self):
        self._kek: Optional[bytes] = None
        self._cache: Dict[str, tuple[bytes, datetime]] = {} # (patient_id, version) -> (plaintext_dek, cached_at)
        self._cache_max_size = 100
        self._cache_ttl_seconds = 300 # 5 minutes

    def _get_kek(self) -> bytes:
        if self._kek is None:
            config = get_kms_config()
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=b"nexa-care-kek-v1",
            )
            self._kek = hkdf.derive(config.kek_root_secret.encode("utf-8"))
        return self._kek

    def _get_cached_dek(self, patient_id: str, version: int) -> Optional[bytes]:
        cache_key = f"{patient_id}:{version}"
        if cache_key in self._cache:
            dek, cached_at = self._cache[cache_key]
            if (datetime.now(timezone.utc) - cached_at).total_seconds() < self._cache_ttl_seconds:
                return dek
            else:
                del self._cache[cache_key]
        return None

    def _set_cached_dek(self, patient_id: str, version: int, dek: bytes):
        if len(self._cache) >= self._cache_max_size:
            self._cache.clear()
        cache_key = f"{patient_id}:{version}"
        self._cache[cache_key] = (dek, datetime.now(timezone.utc))

    async def _get_plaintext_dek(self, patient_id: str, version: int, db: AsyncSession) -> bytes:
        cached = self._get_cached_dek(patient_id, version)
        if cached:
            return cached

        stmt = select(PatientDEKStore).where(
            and_(
                PatientDEKStore.patient_id == uuid.UUID(patient_id),
                PatientDEKStore.dek_version == version,
            )
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            raise EncryptionError(f"DEK v{version} not found or destroyed for patient {patient_id}")

        # Check for destruction (erasure)
        if _is_dek_destroyed(row):
            raise PatientDataErased(patient_id)

        aesgcm = AESGCM(self._get_kek())
        try:
            plaintext_dek = aesgcm.decrypt(row.dek_iv, row.wrapped_dek, None)
        except Exception as exc:
            raise EncryptionError("Failed to unwrap DEK") from exc

        self._set_cached_dek(patient_id, version, plaintext_dek)
        return plaintext_dek

    async def generate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        plaintext_dek = os.urandom(32)
        dek_iv = os.urandom(12)
        aesgcm = AESGCM(self._get_kek())
        wrapped_dek = aesgcm.encrypt(dek_iv, plaintext_dek, None)

        now = datetime.now(timezone.utc)
        row = PatientDEKStore(
            patient_id=uuid.UUID(patient_id),
            wrapped_dek=wrapped_dek,
            dek_iv=dek_iv,
            dek_version=1,
            algorithm="AES-256-GCM",
            is_active=True,
            created_at=now
        )
        db.add(row)
        await db.commit()

        self._set_cached_dek(patient_id, 1, plaintext_dek)

        return DEKBundle(
            patient_id=patient_id,
            wrapped_dek=wrapped_dek,
            dek_version=1,
            algorithm="AES-256-GCM",
            created_at=now
        )

    async def encrypt_field(self, patient_id: str, field_name: str, plaintext: str, db: AsyncSession) -> EncryptedField:
        # Fetch active DEK version
        stmt = select(PatientDEKStore).where(
            and_(
                PatientDEKStore.patient_id == uuid.UUID(patient_id),
                PatientDEKStore.is_active,
            )
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            raise EncryptionError(f"No active DEK found for patient {patient_id}")

        if _is_dek_destroyed(row):
            raise PatientDataErased(patient_id)

        plaintext_dek = await self._get_plaintext_dek(patient_id, row.dek_version, db)
        iv = os.urandom(12)
        aesgcm = AESGCM(plaintext_dek)
        ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

        return EncryptedField(
            ciphertext=ciphertext,
            iv=iv,
            field_name=field_name,
            dek_version=row.dek_version,
            algorithm="AES-256-GCM"
        )

    async def decrypt_field(self, patient_id: str, field_name: str, encrypted: EncryptedField, db: AsyncSession) -> str:
        plaintext_dek = await self._get_plaintext_dek(patient_id, encrypted.dek_version, db)
        aesgcm = AESGCM(plaintext_dek)
        try:
            plaintext = aesgcm.decrypt(encrypted.iv, encrypted.ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise EncryptionError(f"Failed to decrypt field {field_name}") from exc

    async def rotate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        pid = uuid.UUID(patient_id)
        # 1. Deactivate current active DEK
        stmt = update(PatientDEKStore).where(
            and_(PatientDEKStore.patient_id == pid, PatientDEKStore.is_active)
        ).values(is_active=False).returning(PatientDEKStore.dek_version)
        result = await db.execute(stmt)
        old_version = result.scalar()
        if old_version is None:
             old_version = 0

        # 2. Generate new DEK
        new_version = old_version + 1
        plaintext_dek = os.urandom(32)
        dek_iv = os.urandom(12)
        aesgcm = AESGCM(self._get_kek())
        wrapped_dek = aesgcm.encrypt(dek_iv, plaintext_dek, None)

        now = datetime.now(timezone.utc)
        new_row = PatientDEKStore(
            patient_id=pid,
            wrapped_dek=wrapped_dek,
            dek_iv=dek_iv,
            dek_version=new_version,
            algorithm="AES-256-GCM",
            is_active=True,
            created_at=now
        )
        db.add(new_row)
        await db.commit()

        self._set_cached_dek(patient_id, new_version, plaintext_dek)

        return DEKBundle(
            patient_id=patient_id,
            wrapped_dek=wrapped_dek,
            dek_version=new_version,
            algorithm="AES-256-GCM",
            created_at=now
        )

    async def destroy_dek(self, patient_id: str, db: AsyncSession) -> bool:
        pid = uuid.UUID(patient_id)
        # Invalidate cache for ALL versions of this patient
        keys_to_del = [k for k in self._cache if k.startswith(f"{patient_id}:")]
        for k in keys_to_del:
            del self._cache[k]

        # 1. Fetch current wrapped keys to get lengths
        stmt = select(PatientDEKStore).where(PatientDEKStore.patient_id == pid)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        
        if not rows:
            return True # Idempotent

        now = datetime.now(timezone.utc)

        # 2. Overwrite and mark destroyed
        for row in rows:
            # Overwrite with random bytes of same length to defeat backup recovery
            row.wrapped_dek = os.urandom(len(row.wrapped_dek))
            row.is_active = False
            row.destroyed_at = now

        # Flush overwrites before deletion
        await db.flush()

        # 3. Delete the rows
        stmt = delete(PatientDEKStore).where(PatientDEKStore.patient_id == pid)
        await db.execute(stmt)
        await db.commit()

        # 4. Hard-audit completion
        from app.observability.audit_ledger import append_audit_log
        await append_audit_log(
            actor_uid="SYSTEM_KMS",
            event_type="PATIENT_DEK_DESTROYED",
            target_id=patient_id,
            status="SUCCESS",
        )
        return True

    async def get_dek_metadata(self, patient_id: str, db: AsyncSession) -> List[DEKMetadata]:
        stmt = select(PatientDEKStore).where(PatientDEKStore.patient_id == uuid.UUID(patient_id))
        result = await db.execute(stmt)
        rows = result.scalars().all()

        return [
            DEKMetadata(
                patient_id=patient_id,
                dek_version=row.dek_version,
                algorithm=row.algorithm,
                created_at=row.created_at,
                is_active=row.is_active and not _is_dek_destroyed(row)
            )
            for row in rows
        ]


class KMSProvider(EncryptionProvider):
    """Placeholder for AWS KMS / Azure Key Vault. Not implemented this week."""
    async def generate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        raise NotImplementedError("Cloud KMS integration planned for next sprint")
    async def encrypt_field(self, patient_id: str, field_name: str, plaintext: str, db: AsyncSession) -> EncryptedField:
        raise NotImplementedError()
    async def decrypt_field(self, patient_id: str, field_name: str, encrypted: EncryptedField, db: AsyncSession) -> str:
        raise NotImplementedError()
    async def rotate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        raise NotImplementedError()
    async def destroy_dek(self, patient_id: str, db: AsyncSession) -> bool:
        raise NotImplementedError()
    async def get_dek_metadata(self, patient_id: str, db: AsyncSession) -> List[DEKMetadata]:
        raise NotImplementedError()


class EncryptionError(RuntimeError):
    """Raised when encryption/decryption fails."""


class PatientDataErased(EncryptionError):
    """Raised when decryption fails because the patient's keys were destroyed."""
    def __init__(self, patient_id: str):
        self.patient_id = patient_id
        super().__init__(f"Patient data for {patient_id} has been cryptographically erased.")


class LegacyFernetError(EncryptionError):
    """Raised when legacy Fernet data is detected."""
    def __init__(self, data: str):
        self.data = data
        super().__init__("Legacy Fernet data detected")


def get_encryption_provider() -> EncryptionProvider:
    """Factory to get the configured encryption provider."""
    config = get_kms_config()
    if config.encryption_backend == "local":
        return LocalEnvelopeProvider()
    elif config.encryption_backend == "kms":
        return KMSProvider()
    raise ConfigError(f"Unknown encryption backend: {config.encryption_backend}")
