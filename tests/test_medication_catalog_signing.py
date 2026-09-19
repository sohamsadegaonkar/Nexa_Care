import asyncio

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from app.services.medication_catalog_signing import (
    AwsKmsMedicationCatalogSigningProvider,
    InMemoryMedicationCatalogTestSigner,
    MedicationCatalogSigningError,
    build_production_medication_catalog_signer,
    signature_from_text,
    signature_to_text,
)


DIGEST = bytes.fromhex("11" * 32)


def _run(value):
    return asyncio.run(value)


def test_in_memory_signer_is_explicit_test_only_and_detects_tampering() -> None:
    signer = InMemoryMedicationCatalogTestSigner()
    signature = _run(signer.sign_release_digest(DIGEST))
    assert signer.key_identifier.startswith("test-only://")
    assert _run(signer.verify_release_signature(DIGEST, signature)) is True
    assert _run(
        signer.verify_release_signature(bytes.fromhex("22" * 32), signature)
    ) is False
    assert _run(signer.verify_release_signature(DIGEST, b"malformed")) is False


def test_wrong_private_key_signature_is_rejected() -> None:
    signer = InMemoryMedicationCatalogTestSigner()
    wrong_key = ec.derive_private_key(2, ec.SECP256R1())
    wrong_signature = wrong_key.sign(
        DIGEST,
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
    assert _run(signer.verify_release_signature(DIGEST, wrong_signature)) is False


def test_signature_text_round_trip_and_malformed_input() -> None:
    signer = InMemoryMedicationCatalogTestSigner()
    signature = _run(signer.sign_release_digest(DIGEST))
    assert signature_from_text(signature_to_text(signature)) == signature
    with pytest.raises(MedicationCatalogSigningError, match="CATALOG_SIGNATURE_INVALID"):
        signature_from_text("***not-base64***")


class _FakeKms:
    def __init__(self, *, metadata=None, fail=False):
        self._key = ec.derive_private_key(3, ec.SECP256R1())
        self.metadata = metadata or {
            "KeyState": "Enabled",
            "KeyUsage": "SIGN_VERIFY",
            "KeySpec": "ECC_NIST_P256",
            "SigningAlgorithms": ["ECDSA_SHA_256"],
        }
        self.fail = fail
        self.calls = []

    def describe_key(self, *, KeyId):
        self.calls.append(("describe_key", KeyId))
        if self.fail:
            raise RuntimeError("synthetic kms outage")
        return {"KeyMetadata": self.metadata}

    def sign(self, **kwargs):
        self.calls.append(("sign", kwargs))
        return {
            "Signature": self._key.sign(
                kwargs["Message"],
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
        }

    def verify(self, **kwargs):
        self.calls.append(("verify", kwargs))
        try:
            self._key.public_key().verify(
                kwargs["Signature"],
                kwargs["Message"],
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
        except Exception:
            return {"SignatureValid": False}
        return {"SignatureValid": True}


def test_kms_signer_uses_digest_mode_and_expected_algorithm() -> None:
    kms = _FakeKms()
    signer = AwsKmsMedicationCatalogSigningProvider(
        kms_client=kms,
        key_id="alias/synthetic-catalog",
    )
    signature = _run(signer.sign_release_digest(DIGEST))
    assert _run(signer.verify_release_signature(DIGEST, signature)) is True
    sign_call = next(item[1] for item in kms.calls if item[0] == "sign")
    verify_call = next(item[1] for item in kms.calls if item[0] == "verify")
    for call in (sign_call, verify_call):
        assert call["KeyId"] == "alias/synthetic-catalog"
        assert call["MessageType"] == "DIGEST"
        assert call["SigningAlgorithm"] == "ECDSA_SHA_256"
        assert call["Message"] == DIGEST
    assert "PrivateKey" not in sign_call
    assert "PrivateKey" not in verify_call


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "KeyState": "Disabled",
            "KeyUsage": "SIGN_VERIFY",
            "KeySpec": "ECC_NIST_P256",
            "SigningAlgorithms": ["ECDSA_SHA_256"],
        },
        {
            "KeyState": "Enabled",
            "KeyUsage": "ENCRYPT_DECRYPT",
            "KeySpec": "SYMMETRIC_DEFAULT",
            "SigningAlgorithms": [],
        },
        {
            "KeyState": "Enabled",
            "KeyUsage": "SIGN_VERIFY",
            "KeySpec": "RSA_2048",
            "SigningAlgorithms": ["RSASSA_PSS_SHA_256"],
        },
    ],
)
def test_kms_signer_rejects_wrong_key_metadata(metadata) -> None:
    signer = AwsKmsMedicationCatalogSigningProvider(
        kms_client=_FakeKms(metadata=metadata),
        key_id="alias/synthetic-catalog",
    )
    with pytest.raises(
        MedicationCatalogSigningError,
        match="CATALOG_SIGNING_KEY_NOT_READY",
    ):
        _run(signer.assert_ready())


def test_kms_outage_fails_closed() -> None:
    signer = AwsKmsMedicationCatalogSigningProvider(
        kms_client=_FakeKms(fail=True),
        key_id="alias/synthetic-catalog",
    )
    with pytest.raises(
        MedicationCatalogSigningError,
        match="CATALOG_SIGNING_PROVIDER_UNAVAILABLE",
    ):
        _run(signer.sign_release_digest(DIGEST))


def test_production_builder_has_no_test_signer_or_missing_key_fallback() -> None:
    base = {
        "ENVIRONMENT": "pilot",
        "AWS_REGION": "ap-south-1",
        "MEDICATION_CATALOG_SIGNING_KEY_ID": "alias/synthetic-catalog",
    }
    provider = build_production_medication_catalog_signer(
        base,
        kms_client=_FakeKms(),
    )
    assert isinstance(provider, AwsKmsMedicationCatalogSigningProvider)

    with pytest.raises(
        MedicationCatalogSigningError,
        match="TEST_SIGNER_FORBIDDEN",
    ):
        build_production_medication_catalog_signer(
            {**base, "MEDICATION_CATALOG_TEST_SIGNER": "true"},
            kms_client=_FakeKms(),
        )
    with pytest.raises(
        MedicationCatalogSigningError,
        match="CATALOG_SIGNING_KEY_REQUIRED",
    ):
        build_production_medication_catalog_signer(
            {**base, "MEDICATION_CATALOG_SIGNING_KEY_ID": ""},
            kms_client=_FakeKms(),
        )
    with pytest.raises(
        MedicationCatalogSigningError,
        match="PRODUCTION_CATALOG_SIGNER_ENVIRONMENT_REQUIRED",
    ):
        build_production_medication_catalog_signer(
            {**base, "ENVIRONMENT": "development"},
            kms_client=_FakeKms(),
        )
