"""DEFECT 7: the fail-closed erasure-registry gate.

These tests deliberately do NOT rely on the conftest.py autouse bypass of
EncryptionProvider._check_erasure_registry -- they patch it back to the
real implementation so the gate's actual behavior is exercised.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest

from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    check_erasure_registry,
    _PatientErasedSignal,
)
from app.services.crypto_kms import (
    EncryptionProvider,
    LocalEnvelopeProvider,
    PatientDataErased,
)

# Captured at collection time, before conftest.py's autouse override_deps
# fixture replaces this with a no-op AsyncMock for the rest of the suite.
_REAL_CHECK_ERASURE_REGISTRY = EncryptionProvider._check_erasure_registry


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeRegistryDB:
    """A DB double that actually understands the tombstone-status query,
    unlike the blanket mocks the rest of the crypto test suite uses."""

    def __init__(self, status: str | None = None, raise_error: bool = False):
        self.status = status
        self.raise_error = raise_error

    async def execute(self, *_args, **_kwargs):
        if self.raise_error:
            raise ConnectionError("db unreachable")
        return _ScalarResult(self.status)


@pytest.mark.asyncio
async def test_no_tombstone_allows_access():
    db = _FakeRegistryDB(status=None)
    await check_erasure_registry("patient-1", db)  # must not raise


@pytest.mark.asyncio
async def test_active_tombstone_denies_access():
    db = _FakeRegistryDB(status="access_blocked")
    with pytest.raises(_PatientErasedSignal):
        await check_erasure_registry("patient-1", db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        "requested",
        "access_blocked",
        "key_disabled",
        "deletion_scheduled",
        "destroyed",
        "operator_action_required",
    ],
)
async def test_every_active_status_denies_access(status):
    db = _FakeRegistryDB(status=status)
    with pytest.raises(_PatientErasedSignal):
        await check_erasure_registry("patient-1", db)


@pytest.mark.asyncio
async def test_registry_query_failure_fails_closed_not_open():
    """A DB error must never be treated as 'not erased'."""
    db = _FakeRegistryDB(raise_error=True)
    with pytest.raises(ErasureRegistryUnavailable):
        await check_erasure_registry("patient-1", db)


@pytest.mark.asyncio
async def test_malformed_status_value_fails_closed():
    """An unrecognized status string is malformed data, not 'no tombstone'."""
    db = _FakeRegistryDB(status="not_a_real_status")
    with pytest.raises(ErasureRegistryUnavailable):
        await check_erasure_registry("patient-1", db)


@pytest.mark.asyncio
async def test_cached_dek_still_goes_through_the_gate():
    """DEFECT 7 headline finding: before this fix, a cache hit in
    _get_plaintext_dek returned the plaintext DEK without ever checking
    erasure status. This proves the registry check now runs even when the
    DEK is already cached (i.e. before the cache short-circuit)."""
    with patch.dict(
        os.environ,
        {
            "KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!",
        },
    ):
        provider = LocalEnvelopeProvider()
        patient_id = str(uuid.uuid4())
        provider._set_cached_dek(patient_id, 1, b"0" * 32)  # prime the cache directly

        db = _FakeRegistryDB(status="access_blocked")
        with patch.object(
            EncryptionProvider,
            "_check_erasure_registry",
            staticmethod(_REAL_CHECK_ERASURE_REGISTRY),
        ):
            with pytest.raises(PatientDataErased):
                await provider._get_plaintext_dek(patient_id, 1, db)


@pytest.mark.asyncio
async def test_cached_dek_returned_when_registry_confirms_no_tombstone():
    with patch.dict(
        os.environ,
        {
            "KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!",
        },
    ):
        provider = LocalEnvelopeProvider()
        patient_id = str(uuid.uuid4())
        provider._set_cached_dek(patient_id, 1, b"1" * 32)

        db = _FakeRegistryDB(status=None)
        with patch.object(
            EncryptionProvider,
            "_check_erasure_registry",
            staticmethod(_REAL_CHECK_ERASURE_REGISTRY),
        ):
            result = await provider._get_plaintext_dek(patient_id, 1, db)
        assert result == b"1" * 32


@pytest.mark.asyncio
async def test_registry_unavailable_propagates_from_get_plaintext_dek():
    with patch.dict(
        os.environ,
        {
            "KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!",
        },
    ):
        provider = LocalEnvelopeProvider()
        patient_id = str(uuid.uuid4())
        provider._set_cached_dek(patient_id, 1, b"2" * 32)

        db = _FakeRegistryDB(raise_error=True)
        with patch.object(
            EncryptionProvider,
            "_check_erasure_registry",
            staticmethod(_REAL_CHECK_ERASURE_REGISTRY),
        ):
            with pytest.raises(ErasureRegistryUnavailable):
                await provider._get_plaintext_dek(patient_id, 1, db)
