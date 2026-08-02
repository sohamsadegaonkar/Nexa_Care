#!/usr/bin/env python3
"""Fail-closed Milestone 6 pilot configuration readiness check."""

from __future__ import annotations

import argparse
import ipaddress
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

EXPECTED_REGION = "ap-south-1"
ALLOWED_ENVIRONMENTS = frozenset({"pilot", "staging", "production"})
REQUIRED_SECRETS = (
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "HANDSHAKE_PEPPER_SECRET",
    "MFA_ENCRYPTION_KEY",
    "PII_ENCRYPTION_KEY",
    "PATIENT_JWT_SECRET",
    "OTP_RATE_LIMIT_HMAC_SECRET",
)
STATIC_AWS_CREDENTIALS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _value(environment: Mapping[str, str], name: str) -> str:
    return environment.get(name, "").strip()


def _require(environment: Mapping[str, str], name: str, errors: list[str]) -> str:
    value = _value(environment, name)
    if not value:
        errors.append(f"{name}: required")
    return value


def _parse_positive_number(
    environment: Mapping[str, str],
    name: str,
    maximum: float,
    errors: list[str],
) -> None:
    raw = _require(environment, name, errors)
    if not raw:
        return
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"{name}: must be numeric")
        return
    if not 0 < value <= maximum:
        errors.append(f"{name}: must be positive and bounded")


def _parse_attempts(environment: Mapping[str, str], errors: list[str]) -> None:
    name = "DOCUMENT_AI_MAX_ATTEMPTS"
    raw = _require(environment, name, errors)
    if not raw:
        return
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name}: must be an integer")
        return
    if not 1 <= value <= 5:
        errors.append(f"{name}: must be between 1 and 5")


def _hostname(value: str) -> str | None:
    try:
        return urlsplit(value).hostname
    except ValueError:
        return None


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return True
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_database(environment: Mapping[str, str], errors: list[str]) -> None:
    value = _require(environment, "DATABASE_URL", errors)
    if not value:
        return
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError:
        parsed = None
        host = None
    if parsed is None or parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        errors.append("DATABASE_URL: must be a PostgreSQL URL")
    if _is_loopback_host(host):
        errors.append("DATABASE_URL: localhost and loopback hosts are forbidden")


def _validate_redis(environment: Mapping[str, str], errors: list[str]) -> None:
    value = _require(environment, "UPSTASH_REDIS_URL", errors)
    if not value:
        return
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError:
        parsed = None
        host = None
    if parsed is None or parsed.scheme != "rediss":
        errors.append("UPSTASH_REDIS_URL: TLS rediss:// is required")
    if _is_loopback_host(host):
        errors.append("UPSTASH_REDIS_URL: localhost and loopback hosts are forbidden")


def _validate_cors(environment: Mapping[str, str], errors: list[str]) -> None:
    raw = _require(environment, "CORS_ALLOWED_ORIGINS", errors)
    if not raw:
        return
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if not origins:
        errors.append("CORS_ALLOWED_ORIGINS: at least one HTTPS origin is required")
        return
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            valid = (
                parsed.scheme == "https"
                and bool(parsed.netloc)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and "*" not in origin
            )
        except ValueError:
            valid = False
        if not valid:
            errors.append("CORS_ALLOWED_ORIGINS: only explicit HTTPS origins are allowed")
            return


def _validate_trusted_hosts(environment: Mapping[str, str], errors: list[str]) -> None:
    raw = _require(environment, "TRUSTED_HOSTS", errors)
    if not raw:
        return
    hosts = [item.strip() for item in raw.split(",") if item.strip()]
    if not hosts or any("*" in host for host in hosts):
        errors.append("TRUSTED_HOSTS: explicit hosts without wildcards are required")


def _validate_networks(
    environment: Mapping[str, str], name: str, errors: list[str]
) -> None:
    raw = _require(environment, name, errors)
    if not raw:
        return
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    if not entries or any("*" in item for item in entries):
        errors.append(f"{name}: explicit addresses or networks are required")
        return
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            errors.append(f"{name}: contains an invalid address or network")
            return
        if network.prefixlen == 0:
            errors.append(f"{name}: public wildcard networks are forbidden")
            return


def validate_configuration(environment: Mapping[str, str]) -> list[str]:
    """Return safe validation errors containing names, never configured values."""

    errors: list[str] = []

    if _value(environment, "ENVIRONMENT").lower() not in ALLOWED_ENVIRONMENTS:
        errors.append("ENVIRONMENT: must be pilot, staging, or production")

    if _value(environment, "DOCUMENT_EXTRACTION_PROVIDER").lower() != "aws_textract":
        errors.append("DOCUMENT_EXTRACTION_PROVIDER: aws_textract is required")
    if _value(environment, "DOCUMENT_AI_AWS_REGION") != EXPECTED_REGION:
        errors.append("DOCUMENT_AI_AWS_REGION: region must be ap-south-1")
    _parse_positive_number(environment, "DOCUMENT_AI_TIMEOUT_SECONDS", 120.0, errors)
    _parse_attempts(environment, errors)
    for name in ("DOCUMENT_AI_API_URL", "DOCUMENT_AI_API_KEY"):
        if name in environment:
            errors.append(f"{name}: legacy remote-provider setting must be absent")

    if _value(environment, "DOCUMENT_STORAGE_PROVIDER").lower() != "s3":
        errors.append("DOCUMENT_STORAGE_PROVIDER: s3 is required")
    _require(environment, "DOCUMENT_STORAGE_S3_BUCKET", errors)
    if _value(environment, "DOCUMENT_STORAGE_S3_REGION") != EXPECTED_REGION:
        errors.append("DOCUMENT_STORAGE_S3_REGION: region must be ap-south-1")
    _require(environment, "DOCUMENT_STORAGE_S3_KMS_KEY_ID", errors)
    _require(environment, "DOCUMENT_STORAGE_ENCRYPTION_KEY", errors)

    if _value(environment, "ENCRYPTION_BACKEND").lower() != "kms":
        errors.append("ENCRYPTION_BACKEND: kms is required")
    if _value(environment, "AWS_REGION") != EXPECTED_REGION:
        errors.append("AWS_REGION: region must be ap-south-1")
    _require(environment, "KMS_KEY_ID", errors)
    if _value(environment, "AWS_PATIENT_SPECIFIC_KMS_KEYS").lower() != "false":
        errors.append("AWS_PATIENT_SPECIFIC_KMS_KEYS: must be explicitly false")

    for name in STATIC_AWS_CREDENTIALS:
        if name in environment:
            errors.append(f"{name}: static AWS credentials are forbidden")

    _validate_database(environment, errors)
    _validate_redis(environment, errors)
    _validate_cors(environment, errors)
    _validate_trusted_hosts(environment, errors)
    _validate_networks(environment, "TRUSTED_PROXY_NETWORKS", errors)
    _validate_networks(environment, "FORWARDED_ALLOW_IPS", errors)

    for name in REQUIRED_SECRETS:
        _require(environment, name, errors)

    if _value(environment, "PUSH_STATUS_TRANSPORT").lower() != "poll":
        errors.append("PUSH_STATUS_TRANSPORT: poll is required")
    auto_commit = _value(environment, "AUTO_COMMIT").lower()
    if auto_commit and auto_commit not in FALSE_VALUES:
        errors.append("AUTO_COMMIT: enabled or ambiguous settings are forbidden")

    return errors


def check_live_aws(environment: Mapping[str, str]) -> bool:
    """Check only the configured AWS readiness metadata without printing it."""

    try:
        import boto3
        from botocore.config import Config

        region = _value(environment, "AWS_REGION")
        session = boto3.Session(region_name=region)
        if session.region_name != EXPECTED_REGION or session.get_credentials() is None:
            raise RuntimeError("AWS SDK identity or region unavailable")

        client_config = Config(
            region_name=EXPECTED_REGION,
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        session.client("sts", config=client_config).get_caller_identity()

        kms = session.client("kms", config=client_config)
        key_ids = {
            _value(environment, "KMS_KEY_ID"),
            _value(environment, "DOCUMENT_STORAGE_S3_KMS_KEY_ID"),
        }
        for key_id in key_ids:
            kms.describe_key(KeyId=key_id)

        session.client("s3", config=client_config).head_bucket(
            Bucket=_value(environment, "DOCUMENT_STORAGE_S3_BUCKET")
        )
    except Exception as exc:
        print(f"ERROR: live AWS readiness check failed ({type(exc).__name__})")
        return False

    print("PASS: AWS identity and configured KMS/S3 metadata are reachable")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-aws",
        action="store_true",
        help="also check task-role identity and configured KMS/S3 metadata",
    )
    arguments = parser.parse_args()

    errors = validate_configuration(os.environ)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print("FAIL: pilot environment is not ready")
        return 1

    print("PASS: static pilot environment configuration is safe")
    print("INFO: runtime AWS credentials must come from the ECS task IAM role")
    if arguments.live_aws and not check_live_aws(os.environ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
