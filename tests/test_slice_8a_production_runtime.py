from __future__ import annotations

import asyncio
import base64

import pytest

from app.core.production_runtime import (
    RuntimePreflightError,
    _verify_aws_runtime_sync,
    get_operations_auth_token,
    repository_migration_heads,
    validate_production_configuration,
    verify_database_runtime,
    verify_redis_runtime,
)


def _key(byte: bytes = b"k") -> str:
    return base64.urlsafe_b64encode(byte * 32).decode("ascii")


def valid_production_environment() -> dict[str, str]:
    return {
        "ENVIRONMENT": "pilot",
        "SUPABASE_URL": "https://synthetic.supabase.example.test",
        "SUPABASE_KEY": "synthetic-supabase-service-key",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@db.example.test:5432/nexa",
        "UPSTASH_REDIS_URL": "rediss://user:pass@redis.example.test:6379/0",
        "HANDSHAKE_PEPPER_SECRET": "h" * 48,
        "MFA_ENCRYPTION_KEY": _key(b"m"),
        "PII_ENCRYPTION_KEY": _key(b"p"),
        "PATIENT_JWT_SECRET": "j" * 48,
        "OTP_RATE_LIMIT_HMAC_SECRET": "o" * 48,
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET": "r" * 48,
        "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET": "c" * 48,
        "OPERATIONS_AUTH_TOKEN": "x" * 48,
        "DOCUMENT_EXTRACTION_PROVIDER": "aws_textract",
        "DOCUMENT_AI_AWS_REGION": "ap-south-1",
        "DOCUMENT_STORAGE_PROVIDER": "s3",
        "DOCUMENT_STORAGE_ENCRYPTION_KEY": _key(b"d"),
        "DOCUMENT_STORAGE_S3_BUCKET": "synthetic-pilot-bucket",
        "DOCUMENT_STORAGE_S3_REGION": "ap-south-1",
        "DOCUMENT_STORAGE_S3_KMS_KEY_ID": "alias/synthetic-storage",
        "ENCRYPTION_BACKEND": "kms",
        "AWS_REGION": "ap-south-1",
        "KMS_KEY_ID": "alias/synthetic-envelope",
        "AWS_PATIENT_SPECIFIC_KMS_KEYS": "false",
        "CORS_ALLOWED_ORIGINS": "https://doctor.example.test",
        "TRUSTED_HOSTS": "api.example.test",
        "TRUSTED_PROXY_NETWORKS": "10.0.0.0/24",
        "FORWARDED_ALLOW_IPS": "10.0.0.0/24",
        "PUSH_STATUS_TRANSPORT": "poll",
        "AUTO_COMMIT": "false",
        "DATABASE_ECHO_SQL": "false",
        "MAX_UPLOAD_BYTES": "20971520",
    }


def test_valid_production_configuration_is_accepted() -> None:
    assert validate_production_configuration(valid_production_environment()) == []


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("TRUSTED_HOSTS", "*", "TRUSTED_HOSTS"),
        ("UPSTASH_REDIS_URL", "redis://redis.example.test:6379", "UPSTASH_REDIS_URL"),
        ("DATABASE_ECHO_SQL", "true", "DATABASE_ECHO_SQL"),
        ("AUTO_COMMIT", "true", "AUTO_COMMIT"),
        ("PUSH_STATUS_TRANSPORT", "websocket", "PUSH_STATUS_TRANSPORT"),
        ("MAX_UPLOAD_BYTES", "20971521", "MAX_UPLOAD_BYTES"),
        ("MFA_ENCRYPTION_KEY", "not-a-fernet-key", "MFA_ENCRYPTION_KEY"),
        ("OPERATIONS_AUTH_TOKEN", "short", "OPERATIONS_AUTH_TOKEN"),
    ],
)
def test_production_configuration_rejects_fail_open_or_unsafe_values(
    name: str, value: str, expected: str
) -> None:
    environment = valid_production_environment()
    environment[name] = value
    errors = validate_production_configuration(environment)
    assert any(error.startswith(expected) for error in errors)


@pytest.mark.parametrize(
    "name",
    [
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET",
        "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET",
        "OPERATIONS_AUTH_TOKEN",
    ],
)
def test_production_configuration_requires_late_bound_runtime_secrets(name: str) -> None:
    environment = valid_production_environment()
    environment.pop(name)
    errors = validate_production_configuration(environment)
    assert f"{name}: required" in errors


def test_production_configuration_rejects_static_aws_credentials() -> None:
    environment = valid_production_environment()
    environment["AWS_ACCESS_KEY_ID"] = "synthetic-static-credential"
    assert "AWS_ACCESS_KEY_ID: static AWS credentials forbidden" in (
        validate_production_configuration(environment)
    )


def test_operations_token_fails_closed_in_production() -> None:
    environment = valid_production_environment()
    environment["OPERATIONS_AUTH_TOKEN"] = "short"
    with pytest.raises(RuntimePreflightError, match="OPERATIONS_AUTH_TOKEN_INVALID"):
        get_operations_auth_token(environment)


def test_repository_has_exactly_one_migration_head() -> None:
    assert len(repository_migration_heads()) == 1


class _Result:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str]]:
        return self._rows


class _Connection:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    async def execute(self, statement):
        sql = str(statement)
        if "alembic_version" in sql:
            return _Result([(self.revision,)])
        return _Result([])


class _ConnectionContext:
    def __init__(self, revision: str) -> None:
        self.connection = _Connection(revision)

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _Engine:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self.revision)


def test_database_preflight_requires_exact_repository_revision() -> None:
    head = repository_migration_heads()[0]
    assert asyncio.run(verify_database_runtime(_Engine(head))) == head
    with pytest.raises(RuntimePreflightError, match="SCHEMA_REVISION_MISMATCH"):
        asyncio.run(verify_database_runtime(_Engine("stale_revision")))


class _Redis:
    def __init__(self, response=True, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    async def ping(self):
        if self.error is not None:
            raise self.error
        return self.response


def test_redis_preflight_fails_closed() -> None:
    asyncio.run(verify_redis_runtime(_Redis(True)))
    with pytest.raises(RuntimePreflightError, match="REDIS_PREFLIGHT_FAILED"):
        asyncio.run(verify_redis_runtime(_Redis(error=ConnectionError("sensitive"))))


class _KmsClient:
    def describe_key(self, *, KeyId: str) -> dict:
        assert KeyId
        return {"KeyMetadata": {"KeyState": "Enabled", "KeyUsage": "ENCRYPT_DECRYPT"}}


class _S3Client:
    def head_bucket(self, *, Bucket: str) -> None:
        assert Bucket == "synthetic-pilot-bucket"

    def get_bucket_encryption(self, *, Bucket: str) -> dict:
        assert Bucket
        return {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms"
                        }
                    }
                ]
            }
        }

    def get_public_access_block(self, *, Bucket: str) -> dict:
        assert Bucket
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }

    def get_bucket_versioning(self, *, Bucket: str) -> dict:
        assert Bucket
        return {"Status": "Enabled"}


class _AwsSession:
    def client(self, name: str, *, config):
        assert config
        if name == "kms":
            return _KmsClient()
        if name == "s3":
            return _S3Client()
        raise AssertionError(name)


def test_aws_runtime_preflight_checks_kms_and_s3_guards() -> None:
    _verify_aws_runtime_sync(valid_production_environment(), session=_AwsSession())
