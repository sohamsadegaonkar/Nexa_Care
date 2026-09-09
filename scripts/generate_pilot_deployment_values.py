#!/usr/bin/env python3
"""Materialize read-only Nexa Care ECS metadata outside the repository.

This tool never obtains secret values and never invokes an AWS mutation API.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPROVED_AWS_COMMANDS = frozenset(
    {
        ("sts", "get-caller-identity"),
        ("iam", "get-role"),
        ("ecr", "describe-images"),
        ("logs", "describe-log-groups"),
        ("secretsmanager", "describe-secret"),
        ("s3api", "get-bucket-location"),
        ("s3api", "get-bucket-encryption"),
        ("s3api", "get-public-access-block"),
        ("s3api", "get-bucket-versioning"),
        ("kms", "describe-key"),
    }
)


def _outside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return True
    return False


def _validate_aws_command(command: tuple[str, ...]) -> None:
    if len(command) < 2 or not all(isinstance(item, str) and item for item in command):
        raise ValueError("unsafe AWS operation refused")
    if (command[0].lower(), command[1].lower()) not in APPROVED_AWS_COMMANDS:
        raise ValueError("unsafe AWS operation refused")


def _aws(profile: str, region: str, *command: str) -> dict:
    _validate_aws_command(command)
    try:
        result = subprocess.run(
            [
                "aws",
                "--profile",
                profile,
                "--region",
                region,
                *command,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("AWS metadata command failed") from exc
    if result.returncode:
        raise RuntimeError("AWS metadata command failed")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AWS metadata command returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("AWS metadata response was not an object")
    return parsed


def _required_string(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError("AWS metadata response was incomplete")
    return value


def _validate_bucket_security(arguments: argparse.Namespace) -> None:
    location_payload = _aws(
        arguments.storage_profile,
        arguments.region,
        "s3api",
        "get-bucket-location",
        "--bucket",
        arguments.bucket,
    )
    location = location_payload.get("LocationConstraint") or "us-east-1"
    if location != arguments.region:
        raise RuntimeError("qualification bucket is in the wrong region")

    encryption = _aws(
        arguments.storage_profile,
        arguments.region,
        "s3api",
        "get-bucket-encryption",
        "--bucket",
        arguments.bucket,
    )
    rules = encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
    if not any(
        isinstance(rule, dict)
        and rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
        == "aws:kms"
        for rule in rules
    ):
        raise RuntimeError("qualification bucket default encryption is not SSE-KMS")

    public_block = _aws(
        arguments.storage_profile,
        arguments.region,
        "s3api",
        "get-public-access-block",
        "--bucket",
        arguments.bucket,
    ).get("PublicAccessBlockConfiguration", {})
    if not all(
        public_block.get(name) is True
        for name in (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
    ):
        raise RuntimeError("qualification bucket public access block is incomplete")

    versioning = _aws(
        arguments.storage_profile,
        arguments.region,
        "s3api",
        "get-bucket-versioning",
        "--bucket",
        arguments.bucket,
    )
    if versioning.get("Status") != "Enabled":
        raise RuntimeError("qualification bucket versioning is not enabled")


def generate(arguments: argparse.Namespace) -> dict[str, str]:
    if not _outside_repo(arguments.output):
        raise ValueError("output must be outside repository")
    _aws(arguments.profile, arguments.region, "sts", "get-caller-identity")
    execution = _aws(
        arguments.profile,
        arguments.region,
        "iam",
        "get-role",
        "--role-name",
        arguments.execution_role_name,
    )
    task = _aws(
        arguments.profile,
        arguments.region,
        "iam",
        "get-role",
        "--role-name",
        arguments.task_role_name,
    )
    image = _aws(
        arguments.profile,
        arguments.region,
        "ecr",
        "describe-images",
        "--repository-name",
        arguments.repository_name,
        "--image-ids",
        f"imageTag={arguments.image_tag}",
    )
    details = image.get("imageDetails")
    if not isinstance(details, list) or len(details) != 1:
        raise RuntimeError("qualified ECR image was not found")
    digest = _required_string(details[0], "imageDigest")
    repository = _required_string(details[0], "registryId")

    _validate_bucket_security(arguments)

    for key_id in (arguments.envelope_kms_key_id, arguments.storage_kms_key_id):
        metadata = _aws(
            arguments.profile,
            arguments.region,
            "kms",
            "describe-key",
            "--key-id",
            key_id,
        )
        key = metadata.get("KeyMetadata", {})
        if key.get("KeyState") != "Enabled" or key.get("KeyUsage") != "ENCRYPT_DECRYPT":
            raise RuntimeError("qualification KMS key is not enabled for encryption")

    _aws(
        arguments.profile,
        arguments.region,
        "logs",
        "describe-log-groups",
        "--log-group-name-prefix",
        arguments.log_group_name,
    )
    for secret_id in (arguments.runtime_secret_id, arguments.storage_secret_id):
        _aws(
            arguments.profile,
            arguments.region,
            "secretsmanager",
            "describe-secret",
            "--secret-id",
            secret_id,
        )

    return {
        "TASK_CPU": "512",
        "TASK_MEMORY": "1024",
        "ECS_EXECUTION_ROLE_ARN": _required_string(execution.get("Role", {}), "Arn"),
        "ECS_TASK_ROLE_ARN": _required_string(task.get("Role", {}), "Arn"),
        "QUALIFIED_ECR_IMAGE_URI_BY_DIGEST": (
            f"{repository}.dkr.ecr.{arguments.region}.amazonaws.com/"
            f"{arguments.repository_name}@{digest}"
        ),
        "DOCUMENT_STORAGE_S3_BUCKET": arguments.bucket,
        "DOCUMENT_STORAGE_S3_KMS_KEY_ID": arguments.storage_kms_key_id,
        "APPLICATION_ENVELOPE_KMS_KEY_ID": arguments.envelope_kms_key_id,
        "CLOUDWATCH_LOG_GROUP": arguments.log_group_name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in (
        "profile",
        "storage-profile",
        "region",
        "repository-name",
        "image-tag",
        "execution-role-name",
        "task-role-name",
        "log-group-name",
        "bucket",
        "runtime-secret-id",
        "storage-secret-id",
        "envelope-kms-key-id",
        "storage-kms-key-id",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        values = generate(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print("PASS: execution role resolved")
    print("PASS: task role resolved")
    print("PASS: immutable image digest resolved")
    print("PASS: bucket encryption/public-block/versioning metadata resolved")
    print("PASS: KMS, log group, and secret metadata resolved")
    print("INFO: secret values were not read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
