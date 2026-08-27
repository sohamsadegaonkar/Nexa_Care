"""Key Management System (KMS) for Nexa Care.

Provides per-patient Data Encryption Keys (DEKs) wrapped by a system-wide
Key Encryption Key (KEK). Supports versioning and rotation.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import base64
import asyncio
import hashlib
import inspect
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, List

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_kms_config, ConfigError, get_runtime_environment
from app.models.dek_store import PatientDEKStore


def _dek_lifecycle_lock_key(patient_id: str) -> int:
    """Return a stable signed 64-bit PostgreSQL lifecycle-lock key."""
    digest = hashlib.sha256(f"dek-lifecycle:{patient_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _is_postgresql_session(db: AsyncSession) -> bool:
    getter = getattr(db, "get_bind", None)
    if getter is None or not callable(getter):
        return False
    try:
        bind = getter()
        dialect = getattr(bind, "dialect", None)
        return getattr(dialect, "name", None) == "postgresql"
    except Exception:
        return False


def _is_dek_destroyed(row) -> bool:
    return getattr(row, "destroyed_at", None) is not None


async def _acquire_dek_lifecycle_lock(db: AsyncSession, patient_id: str) -> None:
    """Serialize all DEK lifecycle mutations for one patient on PostgreSQL."""
    if not _is_postgresql_session(db):
        return
    lock_result = db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _dek_lifecycle_lock_key(patient_id)},
    )
    if inspect.isawaitable(lock_result):
        await lock_result


async def _raise_if_destroyed_dek(
    patient_id: str, patient_uuid: uuid.UUID, db: AsyncSession
) -> None:
    """Deny lifecycle mutation for any durable destroyed-key marker."""
    result = await db.execute(
        select(PatientDEKStore).where(
            and_(
                PatientDEKStore.patient_id == patient_uuid,
                PatientDEKStore.destroyed_at.is_not(None),
            )
        )
    )
    if result.scalar_one_or_none() is not None:
        raise PatientDataErased(patient_id)


@dataclass(frozen=True)
class DEKBundle:
    patient_id: str
    wrapped_dek: bytes  # DEK encrypted by KEK
    dek_version: int
    algorithm: str  # e.g., "AES-256-GCM"
    created_at: datetime


@dataclass(frozen=True)
class EncryptedField:
    ciphertext: bytes
    iv: bytes  # initialization vector
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
            if len(raw_data) < 13:  # 12 bytes IV + at least 1 byte ciphertext
                raise EncryptionError("Ciphertext too short")
            iv = raw_data[:12]
            ciphertext = raw_data[12:]
            return cls(
                ciphertext=ciphertext,
                iv=iv,
                field_name=field_name,
                dek_version=version,
                algorithm="AES-256-GCM",
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

    @staticmethod
    async def _check_erasure_registry(patient_id: str, db: AsyncSession) -> None:
        """DEFECT 7: fail-closed gate shared by every decrypt path (local and
        AWS), called before returning ANY DEK -- cached or freshly unwrapped.
        A registry error is never treated as "not erased"."""
        from app.security.erasure_registry import (
            ErasureRegistryUnavailable,
            _PatientErasedSignal,
            check_erasure_registry,
        )

        try:
            await check_erasure_registry(patient_id, db)
        except _PatientErasedSignal:
            raise PatientDataErased(patient_id) from None
        except ErasureRegistryUnavailable:
            raise

    @abstractmethod
    async def generate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        """Generate a new Data Encryption Key for a patient."""

    @abstractmethod
    async def ensure_active_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        """Ensure an active Data Encryption Key exists for patient in caller's transaction.

        Stages and flushes any newly generated DEK row on ``db`` WITHOUT committing.
        Raises PatientDataErased if the patient is erased.
        Fails closed on erasure registry error.
        """

    @abstractmethod
    async def encrypt_field(
        self, patient_id: str, field_name: str, plaintext: str, db: AsyncSession
    ) -> EncryptedField:
        """Encrypt a single field using the patient's DEK."""

    @abstractmethod
    async def decrypt_field(
        self,
        patient_id: str,
        field_name: str,
        encrypted: EncryptedField,
        db: AsyncSession,
    ) -> str:
        """Decrypt a single field using the patient's DEK."""

    @abstractmethod
    async def rotate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        """Generate a new DEK and deactivate the old one."""

    @abstractmethod
    async def destroy_dek(self, patient_id: str, db: AsyncSession) -> bool:
        """Cryptographic erasure: permanently delete all of the patient's DEKs."""

    @abstractmethod
    async def get_dek_metadata(
        self, patient_id: str, db: AsyncSession
    ) -> List[DEKMetadata]:
        """Return metadata about all the patient's DEKs."""


class LocalEnvelopeProvider(EncryptionProvider):
    """Local implementation of envelope encryption using a derived KEK."""

    def __init__(self):
        self._kek: Optional[bytes] = None
        self._cache: Dict[
            str, tuple[bytes, str, datetime]
        ] = {}  # (patient_id, version) -> (plaintext_dek, durable_row_fingerprint, cached_at)
        self._cache_max_size = 100
        self._cache_ttl_seconds = 300  # 5 minutes

    def _get_kek(self) -> bytes:
        if self._kek is None:
            config = get_kms_config()
            if not config.kek_root_secret:
                raise ConfigError(
                    "KEK_ROOT_SECRET is required to unwrap legacy local DEKs"
                )
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=b"nexa-care-kek-v1",
            )
            self._kek = hkdf.derive(config.kek_root_secret.encode("utf-8"))
        return self._kek

    def _derive_patient_kek(self, patient_id: str, patient_key_epoch: str) -> bytes:
        """DEFECT 7: patient-specific wrapping key, deterministic within one
        epoch. `patient_key_epoch` is a random value stored only in
        PatientDEKStore.patient_wrapping_key_id -- once that row is deleted
        (destroy_dek), this exact key can never be re-derived again, because
        the epoch itself (not just patient_id) is required and is not
        recoverable from the root secret alone. This is what makes local
        PATIENT_KEY_DESTROYED assurance truthful, unlike the old shared-KEK
        design where "destroying" a DEK never touched the key that wrapped
        it."""
        config = get_kms_config()
        if not config.kek_root_secret:
            raise ConfigError(
                "KEK_ROOT_SECRET is required to derive patient wrapping keys"
            )
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=f"nexa-care-patient-kek-v1:{patient_id}:{patient_key_epoch}".encode(
                "utf-8"
            ),
        )
        return hkdf.derive(config.kek_root_secret.encode("utf-8"))

    def _resolve_wrapping_key(self, row: "PatientDEKStore") -> bytes:
        """Select the correct KEK to wrap/unwrap `row`'s DEK, based on the
        wrapping-key type recorded on the row itself. Existing shared-key
        rows keep using the global KEK; never silently upgrade their
        assurance by re-deriving a patient key for them."""
        wrapping_key_type = getattr(row, "wrapping_key_type", None) or "shared"
        if wrapping_key_type == "patient":
            epoch = getattr(row, "patient_wrapping_key_id", None)
            if not epoch:
                raise EncryptionError(
                    "Patient-wrapped DEK row is missing its wrapping-key epoch."
                )
            return self._derive_patient_kek(str(row.patient_id), epoch)
        return self._get_kek()

    @staticmethod
    def _cache_identity(row: "PatientDEKStore") -> str:
        """Fingerprint durable, non-plaintext DEK metadata for cache binding.

        The fingerprint is internal cache bookkeeping only.  It is never
        logged, persisted, or exposed outside this provider.
        """
        digest = hashlib.sha256()
        parts = (
            str(row.patient_id).encode("utf-8"),
            str(row.dek_version).encode("ascii"),
            bytes(row.wrapped_dek),
            bytes(row.dek_iv),
            str(row.wrapping_backend).encode("utf-8"),
            str(row.wrapping_key_type).encode("utf-8"),
            (
                str(row.patient_wrapping_key_id).encode("utf-8")
                if row.patient_wrapping_key_id is not None
                else b""
            ),
        )
        for part in parts:
            digest.update(len(part).to_bytes(8, byteorder="big"))
            digest.update(part)
        return digest.hexdigest()

    def _get_cached_dek(
        self, patient_id: str, version: int, cache_identity: str
    ) -> Optional[bytes]:
        cache_key = f"{patient_id}:{version}"
        if cache_key in self._cache:
            dek, cached_identity, cached_at = self._cache[cache_key]
            if (
                datetime.now(timezone.utc) - cached_at
            ).total_seconds() < self._cache_ttl_seconds:
                if cached_identity == cache_identity:
                    return dek
                del self._cache[cache_key]
            else:
                del self._cache[cache_key]
        return None

    def _set_cached_dek(
        self, patient_id: str, version: int, dek: bytes, row: "PatientDEKStore"
    ) -> None:
        if len(self._cache) >= self._cache_max_size:
            self._cache.clear()
        cache_key = f"{patient_id}:{version}"
        self._cache[cache_key] = (
            dek,
            self._cache_identity(row),
            datetime.now(timezone.utc),
        )

    async def _get_plaintext_dek(
        self, patient_id: str, version: int, db: AsyncSession
    ) -> bytes:
        await self._check_erasure_registry(patient_id, db)

        stmt = select(PatientDEKStore).where(
            and_(
                PatientDEKStore.patient_id == uuid.UUID(patient_id),
                PatientDEKStore.dek_version == version,
            )
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            raise EncryptionError(
                f"DEK v{version} not found or destroyed for patient {patient_id}"
            )

        backend = getattr(row, "wrapping_backend", None)
        # Rows created before the wrapping_backend migration are local-wrapped.
        # Non-string values can only arise from lightweight test doubles.
        if not isinstance(backend, str):
            backend = "local-aes-gcm"
        if backend != "local-aes-gcm":
            raise EncryptionError("DEK requires a different wrapping backend")

        # Check for destruction (erasure)
        if _is_dek_destroyed(row):
            raise PatientDataErased(patient_id)

        cached = self._get_cached_dek(patient_id, version, self._cache_identity(row))
        if cached:
            return cached

        aesgcm = AESGCM(self._resolve_wrapping_key(row))
        try:
            plaintext_dek = aesgcm.decrypt(row.dek_iv, row.wrapped_dek, None)
        except Exception as exc:
            raise EncryptionError("Failed to unwrap DEK") from exc

        self._set_cached_dek(patient_id, version, plaintext_dek, row)
        return plaintext_dek

    async def ensure_active_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        await self._check_erasure_registry(patient_id, db)
        try:
            pid = uuid.UUID(patient_id)
        except (ValueError, TypeError) as exc:
            raise EncryptionError(
                f"Invalid patient_id for DEK provisioning: {patient_id}"
            ) from exc

        await _acquire_dek_lifecycle_lock(db, patient_id)

        stmt = select(PatientDEKStore).where(
            and_(
                PatientDEKStore.patient_id == pid,
                PatientDEKStore.is_active,
            )
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            if _is_dek_destroyed(row):
                raise PatientDataErased(patient_id)
            return DEKBundle(
                patient_id=patient_id,
                wrapped_dek=row.wrapped_dek,
                dek_version=row.dek_version,
                algorithm=row.algorithm,
                created_at=row.created_at,
            )

        await _raise_if_destroyed_dek(patient_id, pid, db)

        plaintext_dek = os.urandom(32)
        dek_iv = os.urandom(12)
        patient_key_epoch = os.urandom(16).hex()
        wrapping_kek = self._derive_patient_kek(patient_id, patient_key_epoch)
        aesgcm = AESGCM(wrapping_kek)
        wrapped_dek = aesgcm.encrypt(dek_iv, plaintext_dek, None)

        now = datetime.now(timezone.utc)
        row = PatientDEKStore(
            patient_id=pid,
            wrapped_dek=wrapped_dek,
            dek_iv=dek_iv,
            dek_version=1,
            algorithm="AES-256-GCM",
            wrapping_backend="local-aes-gcm",
            is_active=True,
            created_at=now,
            wrapping_key_type="patient",
            patient_wrapping_key_id=patient_key_epoch,
        )
        db.add(row)
        flush_fn = getattr(db, "flush", None)
        if flush_fn is not None:
            flush_res = flush_fn()
            if inspect.isawaitable(flush_res):
                await flush_res

        # The staged row is authoritative in this transaction.  The cache is
        # bound to its durable-row fingerprint and cannot be reused if a later
        # transaction creates a different row with the same patient/version.
        self._set_cached_dek(patient_id, 1, plaintext_dek, row)

        return DEKBundle(
            patient_id=patient_id,
            wrapped_dek=wrapped_dek,
            dek_version=1,
            algorithm="AES-256-GCM",
            created_at=now,
        )

    async def generate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        await self._check_erasure_registry(patient_id, db)
        try:
            pid = uuid.UUID(patient_id)
        except (ValueError, TypeError) as exc:
            raise EncryptionError(
                f"Invalid patient_id for DEK provisioning: {patient_id}"
            ) from exc

        await _acquire_dek_lifecycle_lock(db, patient_id)
        await _raise_if_destroyed_dek(patient_id, pid, db)

        plaintext_dek = os.urandom(32)
        dek_iv = os.urandom(12)
        patient_key_epoch = os.urandom(16).hex()
        wrapped_dek = AESGCM(
            self._derive_patient_kek(patient_id, patient_key_epoch)
        ).encrypt(dek_iv, plaintext_dek, None)
        now = datetime.now(timezone.utc)
        row = PatientDEKStore(
            patient_id=pid,
            wrapped_dek=wrapped_dek,
            dek_iv=dek_iv,
            dek_version=1,
            algorithm="AES-256-GCM",
            wrapping_backend="local-aes-gcm",
            is_active=True,
            created_at=now,
            wrapping_key_type="patient",
            patient_wrapping_key_id=patient_key_epoch,
        )
        db.add(row)
        await db.commit()
        self._set_cached_dek(patient_id, 1, plaintext_dek, row)
        return DEKBundle(
            patient_id=patient_id,
            wrapped_dek=wrapped_dek,
            dek_version=1,
            algorithm="AES-256-GCM",
            created_at=now,
        )

    async def encrypt_field(
        self, patient_id: str, field_name: str, plaintext: str, db: AsyncSession
    ) -> EncryptedField:
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
            algorithm="AES-256-GCM",
        )

    async def decrypt_field(
        self,
        patient_id: str,
        field_name: str,
        encrypted: EncryptedField,
        db: AsyncSession,
    ) -> str:
        plaintext_dek = await self._get_plaintext_dek(
            patient_id, encrypted.dek_version, db
        )
        aesgcm = AESGCM(plaintext_dek)
        try:
            plaintext = aesgcm.decrypt(encrypted.iv, encrypted.ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise EncryptionError(f"Failed to decrypt field {field_name}") from exc

    async def rotate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        await self._check_erasure_registry(patient_id, db)
        pid = uuid.UUID(patient_id)
        await _acquire_dek_lifecycle_lock(db, patient_id)
        await _raise_if_destroyed_dek(patient_id, pid, db)
        latest_result = await db.execute(
            select(func.max(PatientDEKStore.dek_version)).where(
                PatientDEKStore.patient_id == pid
            )
        )
        new_version = (latest_result.scalar_one_or_none() or 0) + 1
        await db.execute(
            update(PatientDEKStore)
            .where(and_(PatientDEKStore.patient_id == pid, PatientDEKStore.is_active))
            .values(is_active=False)
        )
        plaintext_dek = os.urandom(32)
        dek_iv = os.urandom(12)
        patient_key_epoch = os.urandom(16).hex()
        aesgcm = AESGCM(self._derive_patient_kek(patient_id, patient_key_epoch))
        wrapped_dek = aesgcm.encrypt(dek_iv, plaintext_dek, None)

        now = datetime.now(timezone.utc)
        new_row = PatientDEKStore(
            patient_id=pid,
            wrapped_dek=wrapped_dek,
            dek_iv=dek_iv,
            dek_version=new_version,
            algorithm="AES-256-GCM",
            wrapping_backend="local-aes-gcm",
            is_active=True,
            created_at=now,
            wrapping_key_type="patient",
            patient_wrapping_key_id=patient_key_epoch,
        )
        db.add(new_row)
        await db.commit()

        self._set_cached_dek(patient_id, new_version, plaintext_dek, new_row)

        return DEKBundle(
            patient_id=patient_id,
            wrapped_dek=wrapped_dek,
            dek_version=new_version,
            algorithm="AES-256-GCM",
            created_at=now,
        )

    async def destroy_dek(self, patient_id: str, db: AsyncSession) -> bool:
        from sqlalchemy import select as _select

        from app.models.erasure_tombstone import PatientErasureTombstone
        from app.observability.audit_ledger import append_audit_log
        from app.security.erasure_registry import (
            create_tombstone,
            mark_access_blocked,
            mark_deletion_scheduled,
            mark_destroyed,
            mark_key_disabled,
        )

        pid = uuid.UUID(patient_id)

        existing_tombstone = (
            await db.execute(
                _select(PatientErasureTombstone).where(
                    PatientErasureTombstone.patient_ref == patient_id
                )
            )
        ).scalar_one_or_none()
        if existing_tombstone is not None and existing_tombstone.status in {
            "destroyed",
            "deletion_scheduled",
            "key_disabled",
            "access_blocked",
        }:
            return True  # Idempotent: repeated erasure requests do not redo the work.

        keys_to_del = [k for k in self._cache if k.startswith(f"{patient_id}:")]
        for k in keys_to_del:
            del self._cache[k]

        result = await db.execute(
            select(PatientDEKStore).where(PatientDEKStore.patient_id == pid)
        )
        rows = result.scalars().all()

        has_shared_row = any(
            (getattr(r, "wrapping_key_type", None) or "shared") == "shared"
            for r in rows
        )
        wrapping_key_type = "shared" if (has_shared_row or not rows) else "patient"

        tombstone = existing_tombstone or await create_tombstone(
            db,
            patient_ref=patient_id,
            tenant_id=None,
            wrapping_key_type=wrapping_key_type,
        )

        if not rows:
            await mark_access_blocked(db, tombstone)
            await mark_destroyed(db, tombstone)  # No key ever existed to destroy.
            await db.commit()
            return True

        now = datetime.now(timezone.utc)
        for row in rows:
            # Overwrite in place before deletion -- belt-and-braces, though
            # the row is about to be deleted regardless.
            row.wrapped_dek = os.urandom(len(row.wrapped_dek))
            row.is_active = False
            row.destroyed_at = now
        await db.flush()

        await mark_access_blocked(db, tombstone)

        await db.execute(
            delete(PatientDEKStore).where(PatientDEKStore.patient_id == pid)
        )

        if wrapping_key_type == "shared":
            # Legacy shared-key patient: the wrapping KEK is still live for
            # every other shared-key patient, so we can only truthfully
            # claim application access is blocked -- never more than that.
            await db.commit()
            await append_audit_log(
                audit_context=current_audit_context(AuditDomain.ERASURE),
                actor_uid="SYSTEM_KMS",
                event_type="PATIENT_DEK_ACCESS_BLOCKED",
                target_id=patient_id,
                status="SUCCESS",
                metadata={
                    "assurance_level": "active_access_blocked",
                    "wrapping_key_type": "shared",
                },
            )
            return True

        # Patient-specific key: its epoch was stored only on the row just
        # deleted above, so this exact key can never be re-derived again.
        # The local provider has no external KMS deletion-scheduling window,
        # so it proceeds straight to destroyed (a real, honest difference
        # from the AWS provider, which has a mandatory waiting period).
        await mark_key_disabled(db, tombstone, kms_state="local_key_material_deleted")
        await mark_deletion_scheduled(db, tombstone, scheduled_deletion_date=now)
        await mark_destroyed(db, tombstone)
        await db.commit()

        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.ERASURE),
            actor_uid="SYSTEM_KMS",
            event_type="PATIENT_DEK_DESTROYED",
            target_id=patient_id,
            status="SUCCESS",
            metadata={
                "assurance_level": "patient_key_destroyed",
                "wrapping_key_type": "patient",
            },
        )
        return True

    async def get_dek_metadata(
        self, patient_id: str, db: AsyncSession
    ) -> List[DEKMetadata]:
        stmt = select(PatientDEKStore).where(
            PatientDEKStore.patient_id == uuid.UUID(patient_id)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

        return [
            DEKMetadata(
                patient_id=patient_id,
                dek_version=row.dek_version,
                algorithm=row.algorithm,
                created_at=row.created_at,
                is_active=row.is_active and not _is_dek_destroyed(row),
            )
            for row in rows
        ]


class AWSKMSProvider(LocalEnvelopeProvider):
    """Envelope encryption backed by AWS KMS GenerateDataKey/Decrypt."""

    def __init__(self) -> None:
        super().__init__()
        config = get_kms_config()
        if not config.kms_key_id or not config.aws_region:
            raise ConfigError("AWS KMS configuration is incomplete")
        import boto3

        self._kms_key_id = config.kms_key_id
        self._kms = boto3.client("kms", region_name=config.aws_region)

    @staticmethod
    def _context(patient_id: str, version: int) -> dict[str, str]:
        return {
            "application": "nexa-care",
            "patient_id": patient_id,
            "dek_version": str(version),
        }

    @staticmethod
    def _patient_specific_keys_enabled() -> bool:
        # DEFECT 7: provisioning a real AWS CMK per patient has cost and
        # account-quota implications, so it is opt-in rather than the
        # default -- unlike the local provider, where per-patient
        # derivation is free.
        return (
            os.environ.get("AWS_PATIENT_SPECIFIC_KMS_KEYS", "false").strip().lower()
            == "true"
        )

    async def _provision_patient_kms_key(self, patient_id: str) -> str:
        """Create a dedicated CMK for one patient. No PII in the alias,
        description, or tags -- only a non-identifying stable reference."""
        try:
            created = await asyncio.to_thread(
                self._kms.create_key,
                Description="Nexa Care per-patient DEK-wrapping key",
                KeyUsage="ENCRYPT_DECRYPT",
                Origin="AWS_KMS",
                Tags=[{"TagKey": "application", "TagValue": "nexa-care"}],
            )
            key_id = created["KeyMetadata"]["KeyId"]
            alias_name = f"alias/nexa-care-patient-key-{uuid.uuid5(uuid.NAMESPACE_URL, patient_id).hex}"
            await asyncio.to_thread(
                self._kms.create_alias, AliasName=alias_name, TargetKeyId=key_id
            )
            return key_id
        except Exception as exc:
            raise EncryptionError(
                "Failed to provision patient-specific KMS key"
            ) from exc

    async def _generate_wrapped_key(
        self, patient_id: str, version: int, key_id: str | None = None
    ) -> tuple[bytes, bytes]:
        try:
            response = await asyncio.to_thread(
                self._kms.generate_data_key,
                KeyId=key_id or self._kms_key_id,
                KeySpec="AES_256",
                EncryptionContext=self._context(patient_id, version),
            )
            return bytes(response["Plaintext"]), bytes(response["CiphertextBlob"])
        except Exception as exc:
            raise EncryptionError("KMS data-key generation failed") from exc

    async def _get_plaintext_dek(
        self, patient_id: str, version: int, db: AsyncSession
    ) -> bytes:
        await self._check_erasure_registry(patient_id, db)

        result = await db.execute(
            select(PatientDEKStore).where(
                and_(
                    PatientDEKStore.patient_id == uuid.UUID(patient_id),
                    PatientDEKStore.dek_version == version,
                )
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise EncryptionError(
                f"DEK v{version} not found or destroyed for patient {patient_id}"
            )
        if _is_dek_destroyed(row):
            raise PatientDataErased(patient_id)
        backend = getattr(row, "wrapping_backend", "local-aes-gcm")
        if backend == "local-aes-gcm":
            return await super()._get_plaintext_dek(patient_id, version, db)
        if backend != "aws-kms":
            raise EncryptionError("Unknown DEK wrapping backend")
        cached = self._get_cached_dek(patient_id, version, self._cache_identity(row))
        if cached:
            return cached
        try:
            response = await asyncio.to_thread(
                self._kms.decrypt,
                CiphertextBlob=row.wrapped_dek,
                EncryptionContext=self._context(patient_id, version),
                KeyId=(
                    row.patient_wrapping_key_id
                    if getattr(row, "wrapping_key_type", None) == "patient"
                    else self._kms_key_id
                ),
            )
            plaintext = bytes(response["Plaintext"])
        except Exception as exc:
            raise EncryptionError("KMS DEK unwrap failed") from exc
        self._set_cached_dek(patient_id, version, plaintext, row)
        return plaintext

    async def _persist_generated_dek(
        self, patient_id: str, version: int, db: AsyncSession
    ) -> DEKBundle:
        patient_key_id: str | None = None
        if self._patient_specific_keys_enabled():
            patient_key_id = await self._provision_patient_kms_key(patient_id)
        plaintext, wrapped = await self._generate_wrapped_key(
            patient_id, version, key_id=patient_key_id
        )
        del plaintext
        now = datetime.now(timezone.utc)
        db.add(
            PatientDEKStore(
                patient_id=uuid.UUID(patient_id),
                wrapped_dek=wrapped,
                dek_iv=b"",
                dek_version=version,
                algorithm="AES-256-GCM",
                wrapping_backend="aws-kms",
                is_active=True,
                created_at=now,
                wrapping_key_type=("patient" if patient_key_id else "shared"),
                patient_wrapping_key_id=patient_key_id,
            )
        )
        return DEKBundle(
            patient_id=patient_id,
            wrapped_dek=wrapped,
            dek_version=version,
            algorithm="AES-256-GCM",
            created_at=now,
        )

    async def ensure_active_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        await self._check_erasure_registry(patient_id, db)
        try:
            pid = uuid.UUID(patient_id)
        except (ValueError, TypeError) as exc:
            raise EncryptionError(
                f"Invalid patient_id for DEK provisioning: {patient_id}"
            ) from exc

        await _acquire_dek_lifecycle_lock(db, patient_id)

        stmt = select(PatientDEKStore).where(
            and_(
                PatientDEKStore.patient_id == pid,
                PatientDEKStore.is_active,
            )
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            if _is_dek_destroyed(row):
                raise PatientDataErased(patient_id)
            return DEKBundle(
                patient_id=patient_id,
                wrapped_dek=row.wrapped_dek,
                dek_version=row.dek_version,
                algorithm=row.algorithm,
                created_at=row.created_at,
            )

        await _raise_if_destroyed_dek(patient_id, pid, db)
        if self._patient_specific_keys_enabled():
            raise TransactionalPatientSpecificKMSProvisioningUnsupported()

        bundle = await self._persist_generated_dek(patient_id, 1, db)
        flush_fn = getattr(db, "flush", None)
        if flush_fn is not None:
            flush_res = flush_fn()
            if inspect.isawaitable(flush_res):
                await flush_res
        return bundle

    async def generate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        await self._check_erasure_registry(patient_id, db)
        try:
            pid = uuid.UUID(patient_id)
        except (ValueError, TypeError) as exc:
            raise EncryptionError(
                f"Invalid patient_id for DEK provisioning: {patient_id}"
            ) from exc

        await _acquire_dek_lifecycle_lock(db, patient_id)
        await _raise_if_destroyed_dek(patient_id, pid, db)
        # Preserve legacy behavior: this path may create a patient-specific
        # KMS key and commits immediately.  The transactional ensure path is
        # deliberately stricter because a database rollback cannot undo AWS.
        bundle = await self._persist_generated_dek(patient_id, 1, db)
        await db.commit()
        return bundle

    async def rotate_dek(self, patient_id: str, db: AsyncSession) -> DEKBundle:
        await self._check_erasure_registry(patient_id, db)
        pid = uuid.UUID(patient_id)
        await _acquire_dek_lifecycle_lock(db, patient_id)
        await _raise_if_destroyed_dek(patient_id, pid, db)
        result = await db.execute(
            select(PatientDEKStore.dek_version)
            .where(PatientDEKStore.patient_id == pid)
            .order_by(PatientDEKStore.dek_version.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none() or 0
        bundle = await self._persist_generated_dek(patient_id, latest + 1, db)
        await db.execute(
            update(PatientDEKStore)
            .where(
                and_(
                    PatientDEKStore.patient_id == pid,
                    PatientDEKStore.dek_version != latest + 1,
                    PatientDEKStore.is_active,
                )
            )
            .values(is_active=False)
        )
        await db.commit()
        return bundle

    async def destroy_dek(self, patient_id: str, db: AsyncSession) -> bool:
        from sqlalchemy import select as _select

        from app.models.erasure_tombstone import PatientErasureTombstone
        from app.observability.audit_ledger import append_audit_log
        from app.security.erasure_registry import (
            create_tombstone,
            mark_access_blocked,
            mark_deletion_scheduled,
            mark_destroyed,
            mark_key_disabled,
            mark_operator_action_required,
        )

        pid = uuid.UUID(patient_id)

        existing_tombstone = (
            await db.execute(
                _select(PatientErasureTombstone).where(
                    PatientErasureTombstone.patient_ref == patient_id
                )
            )
        ).scalar_one_or_none()
        if existing_tombstone is not None and existing_tombstone.status in {
            "destroyed",
            "deletion_scheduled",
            "key_disabled",
            "access_blocked",
        }:
            return True  # Idempotent.

        keys_to_del = [k for k in self._cache if k.startswith(f"{patient_id}:")]
        for k in keys_to_del:
            del self._cache[k]

        rows = (
            (
                await db.execute(
                    select(PatientDEKStore).where(PatientDEKStore.patient_id == pid)
                )
            )
            .scalars()
            .all()
        )

        aws_wrapped_rows = [
            r for r in rows if getattr(r, "wrapping_backend", None) == "aws-kms"
        ]
        patient_key_ids = {
            r.patient_wrapping_key_id
            for r in aws_wrapped_rows
            if getattr(r, "wrapping_key_type", None) == "patient"
            and r.patient_wrapping_key_id
        }
        has_shared_row = any(
            (getattr(r, "wrapping_key_type", None) or "shared") == "shared"
            for r in rows
        )
        wrapping_key_type = "shared" if (has_shared_row or not rows) else "patient"

        tombstone = existing_tombstone or await create_tombstone(
            db,
            patient_ref=patient_id,
            tenant_id=None,
            wrapping_key_type=wrapping_key_type,
        )

        if not rows:
            await mark_access_blocked(db, tombstone)
            await mark_destroyed(db, tombstone)
            await db.commit()
            return True

        await db.execute(
            delete(PatientDEKStore).where(PatientDEKStore.patient_id == pid)
        )
        await mark_access_blocked(db, tombstone)

        if wrapping_key_type == "shared" or not patient_key_ids:
            # A shared CMK is still in active use by every other patient --
            # we can never disable or schedule its deletion. Truthfully cap
            # assurance at access-blocked, exactly like the local provider.
            await db.commit()
            await append_audit_log(
                audit_context=current_audit_context(AuditDomain.ERASURE),
                actor_uid="SYSTEM_KMS",
                event_type="PATIENT_DEK_ACCESS_BLOCKED",
                target_id=patient_id,
                status="SUCCESS",
                metadata={
                    "assurance_level": "active_access_blocked",
                    "wrapping_key_type": "shared",
                },
            )
            return True

        # Patient-specific CMK(s): safe to disable and schedule deletion --
        # this key was never shared with any other patient.
        operator_action_needed = False
        for key_id in patient_key_ids:
            try:
                await asyncio.to_thread(self._kms.disable_key, KeyId=key_id)
                description = await asyncio.to_thread(
                    self._kms.describe_key, KeyId=key_id
                )
                kms_state = description["KeyMetadata"].get("KeyState")
            except Exception as exc:
                await mark_operator_action_required(
                    db,
                    tombstone,
                    failure_code=f"disable_key_failed:{type(exc).__name__}",
                )
                operator_action_needed = True
                continue

            try:
                deletion = await asyncio.to_thread(
                    self._kms.schedule_key_deletion,
                    KeyId=key_id,
                    PendingWindowInDays=7,
                )
                await mark_key_disabled(db, tombstone, kms_state=kms_state)
                scheduled_date = deletion.get("DeletionDate") or (
                    datetime.now(timezone.utc)
                )
                await mark_deletion_scheduled(
                    db, tombstone, scheduled_deletion_date=scheduled_date
                )
            except Exception as exc:
                await mark_operator_action_required(
                    db,
                    tombstone,
                    failure_code=f"schedule_key_deletion_failed:{type(exc).__name__}",
                )
                operator_action_needed = True

        await db.commit()

        if operator_action_needed:
            await append_audit_log(
                audit_context=current_audit_context(AuditDomain.ERASURE),
                actor_uid="SYSTEM_KMS",
                event_type="PATIENT_DEK_DESTRUCTION_NEEDS_OPERATOR",
                target_id=patient_id,
                status="FAILED",
                metadata={"wrapping_key_type": "patient"},
            )
            return False

        # AWS enforces a mandatory pending-deletion window (>=7 days) before
        # a scheduled key is actually destroyed -- this synchronous request
        # can only reach "deletion scheduled", never "destroyed", in the
        # same call. scripts/reconcile_erasure_registry.py is what advances
        # the tombstone to PATIENT_KEY_DESTROYED once AWS confirms the key
        # is actually gone.
        await append_audit_log(
            audit_context=current_audit_context(AuditDomain.ERASURE),
            actor_uid="SYSTEM_KMS",
            event_type="PATIENT_DEK_DELETION_SCHEDULED",
            target_id=patient_id,
            status="SUCCESS",
            metadata={
                "assurance_level": "patient_key_deletion_scheduled",
                "wrapping_key_type": "patient",
            },
        )
        return True


class EncryptionError(RuntimeError):
    """Raised when encryption/decryption fails."""


class TransactionalPatientSpecificKMSProvisioningUnsupported(EncryptionError):
    """Raised before an uncommitted transaction could orphan a patient CMK."""

    def __init__(self) -> None:
        super().__init__("TRANSACTIONAL_PATIENT_SPECIFIC_KMS_PROVISIONING_UNSUPPORTED")


class PatientDataErased(EncryptionError):
    """Raised when decryption fails because the patient's keys were destroyed."""

    def __init__(self, patient_id: str):
        self.patient_id = patient_id
        super().__init__(
            f"Patient data for {patient_id} has been cryptographically erased."
        )


class LegacyFernetError(EncryptionError):
    """Raised when legacy Fernet data is detected."""

    def __init__(self, data: str):
        self.data = data
        super().__init__("Legacy Fernet data detected")


def get_encryption_provider() -> EncryptionProvider:
    """Factory to get the configured encryption provider."""
    config = get_kms_config()
    if config.encryption_backend == "local":
        environment = get_runtime_environment()
        if not environment.is_demo_allowed:
            raise ConfigError(
                "Local envelope encryption is forbidden in this environment"
            )
        return LocalEnvelopeProvider()
    elif config.encryption_backend == "kms":
        try:
            from botocore.exceptions import BotoCoreError

            return AWSKMSProvider()
        except ImportError as exc:
            raise EncryptionError("Encryption provider initialization failed") from exc
        except BotoCoreError as exc:
            raise EncryptionError("Encryption provider initialization failed") from exc
    raise ConfigError(f"Unknown encryption backend: {config.encryption_backend}")
