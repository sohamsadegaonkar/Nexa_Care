#!/usr/bin/env python3
"""Fail-closed pilot configuration and read-only AWS readiness check."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.production_runtime import validate_production_configuration  # noqa: E402

EXPECTED_REGION = "ap-south-1"
ALLOWED_ENVIRONMENTS = frozenset({"pilot", "staging", "production"})
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
    names = (
        "DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS",
        "DOCUMENT_AI_JOB_MAX_ATTEMPTS",
        "DOCUMENT_AI_RECONCILIATION_MAX_ATTEMPTS",
    )
    for name in names:
        raw = _require(environment, name, errors)
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            errors.append(f"{name}: must be an integer")
            continue
        if not 1 <= value <= 5:
            errors.append(f"{name}: must be between 1 and 5")


def validate_configuration(environment: Mapping[str, str]) -> list[str]:
    """Return safe validation errors containing names, never configured values."""

    errors = list(validate_production_configuration(environment))

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
    if _value(environment, "DOCUMENT_STORAGE_S3_REGION") != EXPECTED_REGION:
        errors.append("DOCUMENT_STORAGE_S3_REGION: region must be ap-south-1")

    if _value(environment, "ENCRYPTION_BACKEND").lower() != "kms":
        errors.append("ENCRYPTION_BACKEND: kms is required")
    if _value(environment, "AWS_REGION") != EXPECTED_REGION:
        errors.append("AWS_REGION: region must be ap-south-1")
    if _value(environment, "AWS_PATIENT_SPECIFIC_KMS_KEYS").lower() != "false":
        errors.append("AWS_PATIENT_SPECIFIC_KMS_KEYS: must be explicitly false")

    if _value(environment, "PUSH_STATUS_TRANSPORT").lower() != "poll":
        errors.append("PUSH_STATUS_TRANSPORT: poll is required")
    auto_commit = _value(environment, "AUTO_COMMIT").lower()
    if auto_commit and auto_commit not in FALSE_VALUES:
        errors.append("AUTO_COMMIT: enabled or ambiguous settings are forbidden")

    return list(dict.fromkeys(errors))


def check_live_aws(environment: Mapping[str, str]) -> bool:
    """Read-only AWS readiness checks; never print configured identifiers."""

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
            metadata = kms.describe_key(KeyId=key_id).get("KeyMetadata", {})
            if (
                metadata.get("KeyState") != "Enabled"
                or metadata.get("KeyUsage") != "ENCRYPT_DECRYPT"
            ):
                raise RuntimeError("KMS key not ready")

        s3 = session.client("s3", config=client_config)
        bucket = _value(environment, "DOCUMENT_STORAGE_S3_BUCKET")
        s3.head_bucket(Bucket=bucket)
        encryption = s3.get_bucket_encryption(Bucket=bucket)
        rules = encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
        if not any(
            isinstance(rule, dict)
            and rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
            == "aws:kms"
            for rule in rules
        ):
            raise RuntimeError("S3 default encryption not ready")
        public_block = s3.get_public_access_block(Bucket=bucket).get(
            "PublicAccessBlockConfiguration", {}
        )
        if not all(
            public_block.get(name) is True
            for name in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        ):
            raise RuntimeError("S3 public access block not ready")
        if s3.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
            raise RuntimeError("S3 versioning not ready")
    except Exception as exc:
        print(f"ERROR: live AWS readiness check failed ({type(exc).__name__})")
        return False

    print("PASS: AWS identity, KMS, and S3 security posture are reachable")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-aws",
        action="store_true",
        help="also check task-role identity and configured KMS/S3 security metadata",
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
