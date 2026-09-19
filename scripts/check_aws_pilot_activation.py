#!/usr/bin/env python3
"""Offline AWS pilot activation validator and task-definition renderer.

This tool performs no AWS calls. It validates operator-supplied deployment
metadata, renders the checked-in ECS task-definition template, and refuses
unsafe or mutable deployment inputs before any account credentials are needed.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK_TEMPLATE = ROOT / "deploy" / "ecs" / "nexa-care-pilot-task-definition.template.json"
EXPECTED_REGION = "ap-south-1"

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IAM_ROLE_ARN_RE = re.compile(r"^arn:aws:iam::(?P<account>[0-9]{12}):role/[A-Za-z0-9+=,.@_/-]+$")
ECR_IMAGE_RE = re.compile(
    r"^(?P<account>[0-9]{12})\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com/"
    r"(?P<repository>[A-Za-z0-9._/-]+)@(?P<digest>sha256:[0-9a-f]{64})$"
)
ECS_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
ECR_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,255}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
PLACEHOLDER_RE = re.compile(r"^<[^<>]+>$")
TASK_PLACEHOLDER_RE = re.compile(r"<([A-Z0-9_]+)>")

ACCOUNT_INPUTS = (
    "ROLE_ARN",
    "AWS_REGION",
    "ECS_CLUSTER",
    "ECS_SERVICE",
    "ECR_REPOSITORY",
    "CLAMD_ECR_REPOSITORY",
    "API_IMAGE_DIGEST",
    "CLAMD_IMAGE_DIGEST",
    "RUNTIME_SECRET_ID",
    "DOCUMENT_STORAGE_SECRET_ID",
    "API_BASE_URL",
)

SECRET_REFERENCE_KEYS = (
    "DATABASE_URL_SECRET_REFERENCE",
    "REDIS_URL_SECRET_REFERENCE",
    "SUPABASE_URL_SECRET_REFERENCE",
    "SUPABASE_KEY_SECRET_REFERENCE",
    "DOCUMENT_STORAGE_ENCRYPTION_KEY_SECRET_REFERENCE",
    "HANDSHAKE_PEPPER_SECRET_REFERENCE",
    "MFA_ENCRYPTION_KEY_SECRET_REFERENCE",
    "PII_ENCRYPTION_KEY_SECRET_REFERENCE",
    "PATIENT_JWT_SECRET_REFERENCE",
    "OTP_RATE_LIMIT_HMAC_SECRET_REFERENCE",
    "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET_REFERENCE",
    "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET_REFERENCE",
    "OPERATIONS_AUTH_TOKEN_SECRET_REFERENCE",
)

FORBIDDEN_STATIC_CREDENTIALS = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
}

EXPECTED_API_ENVIRONMENT = {
    "ENVIRONMENT": "pilot",
    "DOCUMENT_EXTRACTION_PROVIDER": "aws_textract",
    "DOCUMENT_AI_AWS_REGION": EXPECTED_REGION,
    "DOCUMENT_STORAGE_PROVIDER": "s3",
    "DOCUMENT_STORAGE_S3_REGION": EXPECTED_REGION,
    "ENCRYPTION_BACKEND": "kms",
    "AWS_REGION": EXPECTED_REGION,
    "PATIENT_SOURCE_MALWARE_SCANNER": "clamd",
    "PATIENT_SOURCE_CLAMD_HOST": "127.0.0.1",
    "PATIENT_SOURCE_CLAMD_PORT": "3310",
}


def _outside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def _string(values: dict[str, Any], name: str) -> str:
    value = values.get(name)
    return value.strip() if isinstance(value, str) else ""


def _require(values: dict[str, Any], name: str, errors: list[str]) -> str:
    value = _string(values, name)
    if not value:
        errors.append(f"{name}: required")
    elif PLACEHOLDER_RE.fullmatch(value):
        errors.append(f"{name}: placeholder value must be replaced")
    return value


def _validate_role_arn(value: str, name: str, errors: list[str]) -> str | None:
    match = IAM_ROLE_ARN_RE.fullmatch(value)
    if not match:
        errors.append(f"{name}: valid AWS IAM role ARN required")
        return None
    return match.group("account")


def _validate_secret_reference(value: str, name: str, account: str | None, errors: list[str]) -> None:
    prefix = f"arn:aws:secretsmanager:{EXPECTED_REGION}:"
    if not value.startswith(prefix) or ":secret:" not in value:
        errors.append(f"{name}: ap-south-1 Secrets Manager ARN required")
        return
    if account is not None and not value.startswith(f"{prefix}{account}:secret:"):
        errors.append(f"{name}: account must match ROLE_ARN")


def _validate_image(
    *,
    image: str,
    digest: str,
    repository: str,
    account: str | None,
    label: str,
    errors: list[str],
) -> None:
    match = ECR_IMAGE_RE.fullmatch(image)
    if not match:
        errors.append(f"{label}: digest-pinned ap-south-1 ECR image URI required")
        return
    if match.group("region") != EXPECTED_REGION:
        errors.append(f"{label}: region must be {EXPECTED_REGION}")
    if match.group("repository") != repository:
        errors.append(f"{label}: repository does not match activation input")
    if match.group("digest") != digest:
        errors.append(f"{label}: digest does not match activation input")
    if account is not None and match.group("account") != account:
        errors.append(f"{label}: account must match ROLE_ARN")


def validate_activation_values(values: dict[str, Any]) -> list[str]:
    """Validate account-owner activation inputs without reading AWS."""

    errors: list[str] = []
    for name in ACCOUNT_INPUTS:
        _require(values, name, errors)

    if "OPERATIONS_AUTH_TOKEN" in values:
        errors.append("OPERATIONS_AUTH_TOKEN: raw token values must not be stored in the activation file")

    region = _string(values, "AWS_REGION")
    if region and region != EXPECTED_REGION:
        errors.append(f"AWS_REGION: must equal {EXPECTED_REGION}")

    role_account = None
    role_arn = _string(values, "ROLE_ARN")
    if role_arn and not PLACEHOLDER_RE.fullmatch(role_arn):
        role_account = _validate_role_arn(role_arn, "ROLE_ARN", errors)

    for name in ("ECS_EXECUTION_ROLE_ARN", "ECS_TASK_ROLE_ARN"):
        value = _require(values, name, errors)
        if value and not PLACEHOLDER_RE.fullmatch(value):
            account = _validate_role_arn(value, name, errors)
            if role_account is not None and account is not None and account != role_account:
                errors.append(f"{name}: account must match ROLE_ARN")

    for name in ("ECS_CLUSTER", "ECS_SERVICE"):
        value = _string(values, name)
        if value and not PLACEHOLDER_RE.fullmatch(value) and not ECS_NAME_RE.fullmatch(value):
            errors.append(f"{name}: invalid ECS name")

    for name in ("ECR_REPOSITORY", "CLAMD_ECR_REPOSITORY"):
        value = _string(values, name)
        if value and not PLACEHOLDER_RE.fullmatch(value) and not ECR_REPOSITORY_RE.fullmatch(value):
            errors.append(f"{name}: invalid ECR repository name")

    for name in ("API_IMAGE_DIGEST", "CLAMD_IMAGE_DIGEST"):
        value = _string(values, name)
        if value and not PLACEHOLDER_RE.fullmatch(value) and not DIGEST_RE.fullmatch(value):
            errors.append(f"{name}: immutable sha256 digest required")

    api_digest = _string(values, "API_IMAGE_DIGEST")
    clamd_digest = _string(values, "CLAMD_IMAGE_DIGEST")
    api_repo = _string(values, "ECR_REPOSITORY")
    clamd_repo = _string(values, "CLAMD_ECR_REPOSITORY")

    api_image = _require(values, "QUALIFIED_ECR_IMAGE_URI_BY_DIGEST", errors)
    if all((api_image, api_digest, api_repo)) and not any(
        PLACEHOLDER_RE.fullmatch(v) for v in (api_image, api_digest, api_repo)
    ):
        _validate_image(
            image=api_image,
            digest=api_digest,
            repository=api_repo,
            account=role_account,
            label="QUALIFIED_ECR_IMAGE_URI_BY_DIGEST",
            errors=errors,
        )

    clamd_image = _require(values, "QUALIFIED_CLAMD_IMAGE_URI_BY_DIGEST", errors)
    if all((clamd_image, clamd_digest, clamd_repo)) and not any(
        PLACEHOLDER_RE.fullmatch(v) for v in (clamd_image, clamd_digest, clamd_repo)
    ):
        _validate_image(
            image=clamd_image,
            digest=clamd_digest,
            repository=clamd_repo,
            account=role_account,
            label="QUALIFIED_CLAMD_IMAGE_URI_BY_DIGEST",
            errors=errors,
        )

    for name in SECRET_REFERENCE_KEYS:
        value = _require(values, name, errors)
        if value and not PLACEHOLDER_RE.fullmatch(value):
            _validate_secret_reference(value, name, role_account, errors)

    for name in ("TASK_CPU", "TASK_MEMORY"):
        _require(values, name, errors)
    if _string(values, "TASK_CPU") not in {"", "1024", "<TASK_CPU>"}:
        errors.append("TASK_CPU: qualification value must be 1024")
    if _string(values, "TASK_MEMORY") not in {"", "3072", "<TASK_MEMORY>"}:
        errors.append("TASK_MEMORY: qualification value must be 3072")

    base_url = _string(values, "API_BASE_URL")
    if base_url and not PLACEHOLDER_RE.fullmatch(base_url):
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            errors.append("API_BASE_URL: clean https origin required")

    api_host = _require(values, "FINAL_API_HOST", errors)
    if api_host and not PLACEHOLDER_RE.fullmatch(api_host):
        if not HOST_RE.fullmatch(api_host) or "/" in api_host:
            errors.append("FINAL_API_HOST: hostname only")

    doctor_origin = _require(values, "FINAL_DOCTOR_HTTPS_ORIGIN", errors)
    if doctor_origin and not PLACEHOLDER_RE.fullmatch(doctor_origin):
        parsed = urlparse(doctor_origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}:
            errors.append("FINAL_DOCTOR_HTTPS_ORIGIN: https origin required")

    for name in ("FINAL_TRUSTED_PROXY_CIDRS", "FINAL_FORWARDED_PROXY_CIDRS"):
        value = _require(values, name, errors)
        if value and not PLACEHOLDER_RE.fullmatch(value):
            lowered = {item.strip() for item in value.split(",")}
            if not lowered or {"0.0.0.0/0", "::/0", "*"} & lowered:
                errors.append(f"{name}: wildcard/public CIDRs are forbidden")

    for name in (
        "DOCUMENT_STORAGE_S3_BUCKET",
        "DOCUMENT_STORAGE_S3_KMS_KEY_ID",
        "APPLICATION_ENVELOPE_KMS_KEY_ID",
        "CLOUDWATCH_LOG_GROUP",
        "RUNTIME_SECRET_ID",
        "DOCUMENT_STORAGE_SECRET_ID",
    ):
        _require(values, name, errors)

    for name, value in values.items():
        if name in FORBIDDEN_STATIC_CREDENTIALS:
            errors.append(f"{name}: static AWS credentials are forbidden")
        if isinstance(value, str) and ":latest" in value.lower():
            errors.append(f"{name}: latest image tags are forbidden")

    return list(dict.fromkeys(errors))


def _container_environment(container: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name")): str(item.get("value"))
        for item in container.get("environment", [])
        if isinstance(item, dict)
    }


def validate_task_definition(
    task: dict[str, Any],
    values: dict[str, Any] | None = None,
) -> list[str]:
    """Validate rendered ECS topology and provider/security invariants."""

    errors: list[str] = []
    if task.get("networkMode") != "awsvpc":
        errors.append("task.networkMode: awsvpc required")
    if "FARGATE" not in set(task.get("requiresCompatibilities") or []):
        errors.append("task.requiresCompatibilities: FARGATE required")

    containers = {
        item.get("name"): item
        for item in task.get("containerDefinitions", [])
        if isinstance(item, dict)
    }
    expected = {"nexa-care-pilot-api", "patient-source-clamd"}
    if set(containers) != expected:
        errors.append("task.containerDefinitions: exact API + clamd topology required")
        return errors

    api = containers["nexa-care-pilot-api"]
    scanner = containers["patient-source-clamd"]
    if api.get("essential") is not True or scanner.get("essential") is not True:
        errors.append("task.containerDefinitions: API and clamd must both be essential")
    if scanner.get("portMappings"):
        errors.append("patient-source-clamd.portMappings: scanner must not be publicly exposed")

    api_image = str(api.get("image") or "")
    scanner_image = str(scanner.get("image") or "")
    for name, image in (("api.image", api_image), ("scanner.image", scanner_image)):
        if not ECR_IMAGE_RE.fullmatch(image):
            errors.append(f"{name}: digest-pinned ap-south-1 ECR URI required")
        if ":latest" in image.lower():
            errors.append(f"{name}: latest image tag forbidden")

    if values is not None:
        if api_image != _string(values, "QUALIFIED_ECR_IMAGE_URI_BY_DIGEST"):
            errors.append("api.image: does not match qualified activation input")
        if scanner_image != _string(values, "QUALIFIED_CLAMD_IMAGE_URI_BY_DIGEST"):
            errors.append("scanner.image: does not match qualified activation input")
        if task.get("executionRoleArn") != _string(values, "ECS_EXECUTION_ROLE_ARN"):
            errors.append("task.executionRoleArn: does not match activation input")
        if task.get("taskRoleArn") != _string(values, "ECS_TASK_ROLE_ARN"):
            errors.append("task.taskRoleArn: does not match activation input")

    environment = _container_environment(api)
    for name, expected_value in EXPECTED_API_ENVIRONMENT.items():
        if environment.get(name) != expected_value:
            errors.append(f"api.environment.{name}: expected {expected_value}")

    depends_on = api.get("dependsOn") or []
    if {
        "containerName": "patient-source-clamd",
        "condition": "HEALTHY",
    } not in depends_on:
        errors.append("api.dependsOn: healthy clamd dependency required")

    for container in containers.values():
        names = {
            str(item.get("name"))
            for item in container.get("environment", [])
            if isinstance(item, dict)
        }
        names |= {
            str(item.get("name"))
            for item in container.get("secrets", [])
            if isinstance(item, dict)
        }
        if FORBIDDEN_STATIC_CREDENTIALS & names:
            errors.append("task: static AWS credential variables are forbidden")

    for secret in api.get("secrets", []):
        if not isinstance(secret, dict):
            continue
        value_from = str(secret.get("valueFrom") or "")
        if not value_from.startswith(f"arn:aws:secretsmanager:{EXPECTED_REGION}:"):
            errors.append(f"api.secrets.{secret.get('name')}: Secrets Manager ARN required")

    return list(dict.fromkeys(errors))


def render_task_definition(
    template_text: str,
    values: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Render only explicit quoted placeholders, then re-validate JSON and topology."""

    errors: list[str] = []
    placeholders = sorted(set(TASK_PLACEHOLDER_RE.findall(template_text)))
    rendered = template_text
    for name in placeholders:
        value = _string(values, name)
        if not value or PLACEHOLDER_RE.fullmatch(value):
            errors.append(f"{name}: required to render task definition")
            continue
        rendered = rendered.replace(f'"<{name}>"', json.dumps(value))

    remaining = sorted(set(TASK_PLACEHOLDER_RE.findall(rendered)))
    if remaining:
        errors.append("task template: unresolved placeholders: " + ", ".join(remaining))
    if errors:
        return None, errors

    try:
        task = json.loads(rendered)
    except json.JSONDecodeError:
        return None, ["task template: rendered JSON is invalid"]

    if not isinstance(task, dict):
        return None, ["task template: rendered root must be an object"]

    errors.extend(validate_task_definition(task, values))
    return task, list(dict.fromkeys(errors))


def _load_values(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("activation input root must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("values", type=Path)
    parser.add_argument("--task-template", type=Path, default=DEFAULT_TASK_TEMPLATE)
    parser.add_argument("--render-output", type=Path)
    args = parser.parse_args()

    try:
        values = _load_values(args.values)
        template_text = args.task_template.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL: unable to read activation inputs ({type(exc).__name__})")
        return 2

    errors = validate_activation_values(values)
    task, render_errors = render_task_definition(template_text, values)
    errors.extend(render_errors)
    errors = list(dict.fromkeys(errors))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print("FAIL: AWS pilot activation inputs are not safe to deploy")
        return 1

    print("PASS: activation inputs are complete, immutable, and region-consistent")
    print("PASS: rendered task preserves API + task-local clamd topology")
    print("PASS: task uses Textract, S3, KMS, Secrets Manager references, and no static AWS keys")
    print("INFO: no AWS API was called")

    if args.render_output is not None:
        if not _outside_repo(args.render_output):
            print("FAIL: rendered task definition must be written outside the repository")
            return 1
        assert task is not None
        args.render_output.parent.mkdir(parents=True, exist_ok=True)
        args.render_output.write_text(
            json.dumps(task, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print("PASS: rendered task definition written outside repository")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
