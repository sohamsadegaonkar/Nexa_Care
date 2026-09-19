"""Medication-catalog detached-signature boundary.

This module is deliberately separate from patient envelope encryption. The
production signer delegates ECDSA P-256 signing to AWS KMS and never receives
private key bytes.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Mapping
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

CATALOG_SIGNATURE_ALGORITHM = "ECDSA_SHA_256"
CATALOG_KMS_KEY_SPEC = "ECC_NIST_P256"
PRODUCTION_LIKE_ENVIRONMENTS = frozenset(
    {"staging", "preview", "pilot", "production"}
)


class MedicationCatalogSigningError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MedicationCatalogSigningProvider(Protocol):
    @property
    def key_identifier(self) -> str: ...

    @property
    def signature_algorithm(self) -> str: ...

    async def assert_ready(self) -> None: ...

    async def sign_release_digest(self, digest: bytes) -> bytes: ...

    async def verify_release_signature(
        self,
        digest: bytes,
        signature: bytes,
    ) -> bool: ...


def _require_sha256_digest(digest: bytes) -> None:
    if not isinstance(digest, bytes) or len(digest) != 32:
        raise MedicationCatalogSigningError("INVALID_RELEASE_DIGEST")


class AwsKmsMedicationCatalogSigningProvider:
    """AWS KMS SIGN_VERIFY provider for a dedicated non-exportable catalog key."""

    def __init__(self, *, kms_client, key_id: str) -> None:
        clean = key_id.strip()
        if not clean:
            raise MedicationCatalogSigningError("CATALOG_SIGNING_KEY_REQUIRED")
        self._client = kms_client
        self._key_id = clean

    @property
    def key_identifier(self) -> str:
        return self._key_id

    @property
    def signature_algorithm(self) -> str:
        return CATALOG_SIGNATURE_ALGORITHM

    async def assert_ready(self) -> None:
        try:
            response = await asyncio.to_thread(
                self._client.describe_key,
                KeyId=self._key_id,
            )
        except Exception as exc:
            raise MedicationCatalogSigningError(
                "CATALOG_SIGNING_PROVIDER_UNAVAILABLE"
            ) from exc
        metadata = response.get("KeyMetadata", {})
        algorithms = set(metadata.get("SigningAlgorithms") or ())
        if (
            metadata.get("KeyState") != "Enabled"
            or metadata.get("KeyUsage") != "SIGN_VERIFY"
            or metadata.get("KeySpec") != CATALOG_KMS_KEY_SPEC
            or CATALOG_SIGNATURE_ALGORITHM not in algorithms
        ):
            raise MedicationCatalogSigningError("CATALOG_SIGNING_KEY_NOT_READY")

    async def sign_release_digest(self, digest: bytes) -> bytes:
        _require_sha256_digest(digest)
        await self.assert_ready()
        try:
            response = await asyncio.to_thread(
                self._client.sign,
                KeyId=self._key_id,
                Message=digest,
                MessageType="DIGEST",
                SigningAlgorithm=CATALOG_SIGNATURE_ALGORITHM,
            )
        except Exception as exc:
            raise MedicationCatalogSigningError(
                "CATALOG_SIGNING_PROVIDER_UNAVAILABLE"
            ) from exc
        signature = response.get("Signature")
        if not isinstance(signature, bytes) or not signature:
            raise MedicationCatalogSigningError("CATALOG_SIGNATURE_INVALID")
        return signature

    async def verify_release_signature(
        self,
        digest: bytes,
        signature: bytes,
    ) -> bool:
        _require_sha256_digest(digest)
        if not isinstance(signature, bytes) or not signature:
            return False
        await self.assert_ready()
        try:
            response = await asyncio.to_thread(
                self._client.verify,
                KeyId=self._key_id,
                Message=digest,
                MessageType="DIGEST",
                Signature=signature,
                SigningAlgorithm=CATALOG_SIGNATURE_ALGORITHM,
            )
        except Exception as exc:
            raise MedicationCatalogSigningError(
                "CATALOG_SIGNING_PROVIDER_UNAVAILABLE"
            ) from exc
        return response.get("SignatureValid") is True


class InMemoryMedicationCatalogTestSigner:
    """Fixed-key, non-production signer used only through explicit test injection."""

    def __init__(self) -> None:
        self._private_key = ec.derive_private_key(1, ec.SECP256R1())

    @property
    def key_identifier(self) -> str:
        return "test-only://medication-catalog-p256"

    @property
    def signature_algorithm(self) -> str:
        return CATALOG_SIGNATURE_ALGORITHM

    async def assert_ready(self) -> None:
        return None

    async def sign_release_digest(self, digest: bytes) -> bytes:
        _require_sha256_digest(digest)
        return self._private_key.sign(
            digest,
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )

    async def verify_release_signature(
        self,
        digest: bytes,
        signature: bytes,
    ) -> bool:
        _require_sha256_digest(digest)
        try:
            self._private_key.public_key().verify(
                signature,
                digest,
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True


def signature_to_text(signature: bytes) -> str:
    if not isinstance(signature, bytes) or not signature:
        raise MedicationCatalogSigningError("CATALOG_SIGNATURE_INVALID")
    return base64.b64encode(signature).decode("ascii")


def signature_from_text(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise MedicationCatalogSigningError("CATALOG_SIGNATURE_INVALID")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise MedicationCatalogSigningError("CATALOG_SIGNATURE_INVALID") from exc


def build_production_medication_catalog_signer(
    environment: Mapping[str, str] | None = None,
    *,
    kms_client=None,
) -> AwsKmsMedicationCatalogSigningProvider:
    """Construct a production signer without any local-private-key fallback."""

    values = os.environ if environment is None else environment
    runtime = values.get("ENVIRONMENT", "").strip().lower()
    if runtime not in PRODUCTION_LIKE_ENVIRONMENTS:
        raise MedicationCatalogSigningError(
            "PRODUCTION_CATALOG_SIGNER_ENVIRONMENT_REQUIRED"
        )
    if values.get("MEDICATION_CATALOG_TEST_SIGNER", "").strip():
        raise MedicationCatalogSigningError("TEST_SIGNER_FORBIDDEN")
    key_id = values.get("MEDICATION_CATALOG_SIGNING_KEY_ID", "").strip()
    if not key_id:
        raise MedicationCatalogSigningError("CATALOG_SIGNING_KEY_REQUIRED")
    if kms_client is None:
        try:
            import boto3

            kms_client = boto3.client(
                "kms",
                region_name=values.get("AWS_REGION", "").strip() or None,
            )
        except Exception as exc:
            raise MedicationCatalogSigningError(
                "CATALOG_SIGNING_PROVIDER_UNAVAILABLE"
            ) from exc
    return AwsKmsMedicationCatalogSigningProvider(
        kms_client=kms_client,
        key_id=key_id,
    )
