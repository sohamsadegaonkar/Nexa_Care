"""Integration tests for Squad B/C: Biometric Signature Verification.

Contract:
- Use ECDSA P-256 for biometric signatures.
- Nonce challenge provided in push request status.
- Signature required for 'approved' decision.

DEFECT 5 note: BiometricSignatureVerifier.verify_signature() is fully
implemented (real ECDSA P-256 verification, nonce replay protection,
erasure-aware key decryption) but is not called from any HTTP route
anywhere in app/ -- there is no POST /push/{id}/respond endpoint. The
original xfail tests assumed such a route; it does not exist. Testing at
the route level would mean inventing a route that isn't real. These tests
instead exercise the real, production BiometricSignatureVerifier service
directly with genuine ECDSA keypairs -- the actual "route or service
workflow" the defect asks for, since no route exists to test against.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services.biometric_signature_verifier import BiometricSignatureVerifier
from app.services.crypto_kms import EncryptedField, PatientDataErased


def _placeholder_encrypted_field() -> str:
    """A validly-FORMATTED (not meaningfully decryptable) EncryptedField
    serialization -- decrypt_field itself is mocked separately in each
    test, this only needs to satisfy EncryptedField.deserialize()'s format
    check so the real deserialize() call along the way doesn't choke."""
    return EncryptedField(
        ciphertext=b"placeholder-ciphertext",
        iv=b"0" * 12,
        field_name="device_public_key",
        dek_version=1,
        algorithm="AES-256-GCM",
    ).serialize()


def _generate_device_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, base64.b64encode(public_der).decode("utf-8")


def _sign(private_key, nonce: str, request_id: str, patient_id: str) -> str:
    message = f"{nonce}{request_id}{patient_id}".encode("utf-8")
    signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(signature).decode("utf-8")


def _mock_supabase(device_public_key_b64: str | None, revoked_at: str | None = None):
    supabase = MagicMock()
    response = MagicMock()
    response.data = (
        {"device_public_key": _placeholder_encrypted_field(), "revoked_at": revoked_at}
        if device_public_key_b64 is not None else None
    )
    supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = response
    return supabase


def _mock_kms(decrypted_public_key_b64: str):
    kms = AsyncMock()
    kms.decrypt_field = AsyncMock(return_value=decrypted_public_key_b64)
    return kms


@pytest.mark.asyncio
async def test_biometric_approval_flow():
    """Enrolled P-256 device key, current challenge, exact canonical
    payload, valid signature -> verified."""
    private_key, public_key_b64 = _generate_device_keypair()
    patient_id, request_id, nonce = "p-123", "req-abc", "nonce-fresh-1"
    signature_b64 = _sign(private_key, nonce, request_id, patient_id)

    redis = AsyncMock()
    redis.get.return_value = None  # nonce not yet used
    redis.setex = AsyncMock()

    verifier = BiometricSignatureVerifier()
    with patch("app.services.biometric_signature_verifier.get_supabase_client",
               return_value=_mock_supabase(public_key_b64)), \
         patch("app.services.biometric_signature_verifier.get_encryption_provider",
               return_value=_mock_kms(public_key_b64)):
        result = await verifier.verify_signature(
            patient_id=patient_id, request_id=request_id,
            signature_b64=signature_b64, challenge_nonce=nonce,
            redis=redis, db=AsyncMock(),
        )

    assert result.verified is True
    assert result.error is None
    redis.setex.assert_awaited_once()  # nonce consumed on success


@pytest.mark.asyncio
async def test_invalid_signature_fails():
    """Garbage / tampered signature is rejected, not just accepted blindly."""
    _, public_key_b64 = _generate_device_keypair()
    patient_id, request_id, nonce = "p-123", "req-abc", "nonce-2"

    redis = AsyncMock()
    redis.get.return_value = None
    redis.setex = AsyncMock()

    verifier = BiometricSignatureVerifier()
    with patch("app.services.biometric_signature_verifier.get_supabase_client",
               return_value=_mock_supabase(public_key_b64)), \
         patch("app.services.biometric_signature_verifier.get_encryption_provider",
               return_value=_mock_kms(public_key_b64)):
        result = await verifier.verify_signature(
            patient_id=patient_id, request_id=request_id,
            signature_b64=base64.b64encode(b"not-a-real-signature").decode("utf-8"),
            challenge_nonce=nonce, redis=redis, db=AsyncMock(),
        )

    assert result.verified is False
    assert result.error == "Invalid cryptographic signature"
    redis.setex.assert_not_awaited()  # a rejected signature must never consume the nonce


@pytest.mark.asyncio
async def test_wrong_device_key_signature_rejected():
    """Signed with a private key that does NOT match the patient's
    registered device public key -- must be rejected, not silently trusted
    because *a* valid-looking signature was supplied."""
    attacker_key, _ = _generate_device_keypair()
    _, real_public_key_b64 = _generate_device_keypair()
    patient_id, request_id, nonce = "p-123", "req-abc", "nonce-3"
    forged_signature = _sign(attacker_key, nonce, request_id, patient_id)

    redis = AsyncMock()
    redis.get.return_value = None
    redis.setex = AsyncMock()

    verifier = BiometricSignatureVerifier()
    with patch("app.services.biometric_signature_verifier.get_supabase_client",
               return_value=_mock_supabase(real_public_key_b64)), \
         patch("app.services.biometric_signature_verifier.get_encryption_provider",
               return_value=_mock_kms(real_public_key_b64)):
        result = await verifier.verify_signature(
            patient_id=patient_id, request_id=request_id,
            signature_b64=forged_signature, challenge_nonce=nonce,
            redis=redis, db=AsyncMock(),
        )

    assert result.verified is False
    assert result.error == "Invalid cryptographic signature"


@pytest.mark.asyncio
async def test_signature_bound_to_request_id_rejects_cross_request_replay():
    """A signature valid for one request_id must not verify against a
    different request_id, even with a fresh (unused) nonce -- proves the
    signed payload is genuinely bound to the specific request, not just
    the nonce."""
    private_key, public_key_b64 = _generate_device_keypair()
    patient_id, nonce = "p-123", "nonce-4"
    signature_for_req_a = _sign(private_key, nonce, "req-A", patient_id)

    redis = AsyncMock()
    redis.get.return_value = None
    redis.setex = AsyncMock()

    verifier = BiometricSignatureVerifier()
    with patch("app.services.biometric_signature_verifier.get_supabase_client",
               return_value=_mock_supabase(public_key_b64)), \
         patch("app.services.biometric_signature_verifier.get_encryption_provider",
               return_value=_mock_kms(public_key_b64)):
        result = await verifier.verify_signature(
            patient_id=patient_id, request_id="req-B",  # different request
            signature_b64=signature_for_req_a, challenge_nonce=nonce,
            redis=redis, db=AsyncMock(),
        )

    assert result.verified is False


@pytest.mark.asyncio
async def test_revoked_device_key_is_rejected():
    private_key, public_key_b64 = _generate_device_keypair()
    patient_id, request_id, nonce = "p-123", "req-abc", "nonce-5"
    signature_b64 = _sign(private_key, nonce, request_id, patient_id)

    redis = AsyncMock()
    redis.get.return_value = None

    verifier = BiometricSignatureVerifier()
    with patch("app.services.biometric_signature_verifier.get_supabase_client",
               return_value=_mock_supabase(public_key_b64, revoked_at="2026-01-01T00:00:00Z")):
        result = await verifier.verify_signature(
            patient_id=patient_id, request_id=request_id,
            signature_b64=signature_b64, challenge_nonce=nonce,
            redis=redis, db=AsyncMock(),
        )

    assert result.verified is False
    assert result.error == "Biometric binding revoked"


@pytest.mark.asyncio
async def test_device_not_enrolled_is_rejected():
    redis = AsyncMock()
    redis.get.return_value = None

    verifier = BiometricSignatureVerifier()
    with patch("app.services.biometric_signature_verifier.get_supabase_client",
               return_value=_mock_supabase(None)):
        result = await verifier.verify_signature(
            patient_id="unenrolled-patient", request_id="req-abc",
            signature_b64=base64.b64encode(b"x").decode("utf-8"), challenge_nonce="nonce-6",
            redis=redis, db=AsyncMock(),
        )

    assert result.verified is False
    assert result.error == "Device not enrolled for biometric verification"


@pytest.mark.asyncio
async def test_erased_patient_data_propagates_as_patient_data_erased():
    patient_id, request_id, nonce = "p-erased", "req-abc", "nonce-7"
    redis = AsyncMock()
    redis.get.return_value = None

    kms = AsyncMock()
    kms.decrypt_field = AsyncMock(side_effect=PatientDataErased(patient_id))

    verifier = BiometricSignatureVerifier()
    with patch("app.services.biometric_signature_verifier.get_supabase_client",
               return_value=_mock_supabase("some-encrypted-key")), \
         patch("app.services.biometric_signature_verifier.get_encryption_provider", return_value=kms):
        result = await verifier.verify_signature(
            patient_id=patient_id, request_id=request_id,
            signature_b64=base64.b64encode(b"x").decode("utf-8"), challenge_nonce=nonce,
            redis=redis, db=AsyncMock(),
        )

    assert result.verified is False
    assert result.error == "PATIENT_DATA_ERASED"

@pytest.mark.integration
@pytest.mark.asyncio
async def test_signature_replay_fails():
    """Test: Replayed nonce fails before device-key lookup or signature acceptance."""
    verifier = BiometricSignatureVerifier()
    redis = AsyncMock()
    redis.get.return_value = "1"

    result = await verifier.verify_signature(
        patient_id="p-123",
        request_id="req-123",
        signature_b64=base64.b64encode(b"signature").decode("utf-8"),
        challenge_nonce="used-nonce",
        redis=redis,
        db=AsyncMock(),
    )

    assert result.verified is False
    assert result.error == "Nonce already used"
    redis.get.assert_awaited_once_with("biometric_nonce:used-nonce:used")
    redis.setex.assert_not_awaited()