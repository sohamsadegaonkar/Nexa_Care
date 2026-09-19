from __future__ import annotations

import json
from pathlib import Path

from scripts.check_aws_pilot_activation import (
    render_task_definition,
    validate_activation_values,
    validate_task_definition,
)

ROOT = Path(__file__).resolve().parents[1]
TASK_TEMPLATE = ROOT / "deploy" / "ecs" / "nexa-care-pilot-task-definition.template.json"


def _secret(name: str) -> str:
    return f"arn:aws:secretsmanager:ap-south-1:123456789012:secret:{name}-AbCdEf"


def _valid_values() -> dict[str, str]:
    api_digest = "sha256:" + "a" * 64
    scanner_digest = "sha256:" + "b" * 64
    values = {
        "AWS_REGION": "ap-south-1",
        "ROLE_ARN": "arn:aws:iam::123456789012:role/nexa-github-pilot",
        "ECS_CLUSTER": "nexa-pilot",
        "ECS_SERVICE": "nexa-pilot-api",
        "ECR_REPOSITORY": "nexa-care-api",
        "CLAMD_ECR_REPOSITORY": "nexa-care-clamd",
        "API_IMAGE_DIGEST": api_digest,
        "CLAMD_IMAGE_DIGEST": scanner_digest,
        "RUNTIME_SECRET_ID": "nexa/pilot/runtime",
        "DOCUMENT_STORAGE_SECRET_ID": "nexa/pilot/document-storage",
        "API_BASE_URL": "https://api.pilot.example.test",
        "TASK_CPU": "1024",
        "TASK_MEMORY": "3072",
        "ECS_EXECUTION_ROLE_ARN": "arn:aws:iam::123456789012:role/nexa-ecs-execution",
        "ECS_TASK_ROLE_ARN": "arn:aws:iam::123456789012:role/nexa-ecs-runtime",
        "QUALIFIED_ECR_IMAGE_URI_BY_DIGEST": (
            "123456789012.dkr.ecr.ap-south-1.amazonaws.com/"
            f"nexa-care-api@{api_digest}"
        ),
        "QUALIFIED_CLAMD_IMAGE_URI_BY_DIGEST": (
            "123456789012.dkr.ecr.ap-south-1.amazonaws.com/"
            f"nexa-care-clamd@{scanner_digest}"
        ),
        "DOCUMENT_STORAGE_S3_BUCKET": "nexa-synthetic-pilot",
        "DOCUMENT_STORAGE_S3_KMS_KEY_ID": (
            "arn:aws:kms:ap-south-1:123456789012:key/storage"
        ),
        "APPLICATION_ENVELOPE_KMS_KEY_ID": (
            "arn:aws:kms:ap-south-1:123456789012:key/envelope"
        ),
        "CLOUDWATCH_LOG_GROUP": "/nexa/pilot/api",
        "FINAL_API_HOST": "api.pilot.example.test",
        "FINAL_DOCTOR_HTTPS_ORIGIN": "https://doctor.pilot.example.test",
        "FINAL_TRUSTED_PROXY_CIDRS": "10.0.1.0/24",
        "FINAL_FORWARDED_PROXY_CIDRS": "10.0.1.0/24",
    }
    secret_names = (
        "DATABASE_URL",
        "REDIS_URL",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "DOCUMENT_STORAGE_ENCRYPTION_KEY",
        "HANDSHAKE_PEPPER",
        "MFA_ENCRYPTION_KEY",
        "PII_ENCRYPTION_KEY",
        "PATIENT_JWT",
        "OTP_RATE_LIMIT_HMAC",
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC",
        "PROVIDER_CONTACT_ASSURANCE_HMAC",
        "OPERATIONS_AUTH_TOKEN",
    )
    for name in secret_names:
        values[f"{name}_SECRET_REFERENCE"] = _secret(name.lower())
    return values


def _render(values: dict[str, str]) -> dict:
    task, errors = render_task_definition(TASK_TEMPLATE.read_text(encoding="utf-8"), values)
    assert errors == []
    assert task is not None
    return task


def test_valid_activation_values_render_d6_task_without_aws() -> None:
    values = _valid_values()
    assert validate_activation_values(values) == []
    task = _render(values)
    assert validate_task_definition(task, values) == []


def test_activation_rejects_placeholder_or_missing_account_input() -> None:
    values = _valid_values()
    values["ECS_CLUSTER"] = "<PILOT_ECS_CLUSTER>"
    errors = validate_activation_values(values)
    assert "ECS_CLUSTER: placeholder value must be replaced" in errors


def test_activation_rejects_mutable_or_mismatched_images() -> None:
    values = _valid_values()
    values["QUALIFIED_ECR_IMAGE_URI_BY_DIGEST"] = (
        "123456789012.dkr.ecr.ap-south-1.amazonaws.com/nexa-care-api:latest"
    )
    errors = validate_activation_values(values)
    assert any("QUALIFIED_ECR_IMAGE_URI_BY_DIGEST" in error for error in errors)
    assert any("latest image tags are forbidden" in error for error in errors)


def test_activation_rejects_raw_operations_token_and_static_aws_keys() -> None:
    values = _valid_values()
    values["OPERATIONS_AUTH_TOKEN"] = "must-not-be-here"
    values["AWS_ACCESS_KEY_ID"] = "must-not-be-here"
    errors = validate_activation_values(values)
    assert any("raw token values must not be stored" in error for error in errors)
    assert any("static AWS credentials are forbidden" in error for error in errors)


def test_rendered_task_rejects_public_scanner_port() -> None:
    values = _valid_values()
    task = _render(values)
    scanner = next(
        item
        for item in task["containerDefinitions"]
        if item["name"] == "patient-source-clamd"
    )
    scanner["portMappings"] = [{"containerPort": 3310, "hostPort": 3310}]
    errors = validate_task_definition(task, values)
    assert "patient-source-clamd.portMappings: scanner must not be publicly exposed" in errors


def test_rendered_task_rejects_wrong_provider_contract() -> None:
    values = _valid_values()
    task = _render(values)
    api = next(
        item
        for item in task["containerDefinitions"]
        if item["name"] == "nexa-care-pilot-api"
    )
    for item in api["environment"]:
        if item["name"] == "DOCUMENT_EXTRACTION_PROVIDER":
            item["value"] = "remote"
    errors = validate_task_definition(task, values)
    assert "api.environment.DOCUMENT_EXTRACTION_PROVIDER: expected aws_textract" in errors


def test_rendered_task_rejects_plaintext_secret_reference() -> None:
    values = _valid_values()
    task = _render(values)
    api = next(
        item
        for item in task["containerDefinitions"]
        if item["name"] == "nexa-care-pilot-api"
    )
    api["secrets"][0]["valueFrom"] = "plaintext-secret"
    errors = validate_task_definition(task, values)
    assert any("Secrets Manager ARN required" in error for error in errors)


def test_template_contract_contains_no_real_secret_or_account_value() -> None:
    payload = json.loads(
        (ROOT / "deploy" / "ecs" / "pilot-activation-inputs.template.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["AWS_REGION"] == "ap-south-1"
    assert payload["ROLE_ARN"].startswith("<")
    assert payload["OPERATIONS_AUTH_TOKEN_SECRET_REFERENCE"].startswith("<")
    assert "OPERATIONS_AUTH_TOKEN" not in payload


def test_activation_rejects_non_origin_url_and_invalid_proxy_cidr() -> None:
    values = _valid_values()
    values["API_BASE_URL"] = "https://api.pilot.example.test/unexpected-path"
    values["FINAL_TRUSTED_PROXY_CIDRS"] = "not-a-cidr"
    errors = validate_activation_values(values)
    assert "API_BASE_URL: clean https origin required" in errors
    assert "FINAL_TRUSTED_PROXY_CIDRS: valid CIDR required" in errors


def test_activation_schema_requires_account_runtime_and_image_inputs() -> None:
    schema = json.loads(
        (ROOT / "deploy" / "ecs" / "pilot-deployment-values.schema.json").read_text(
            encoding="utf-8"
        )
    )
    required = set(schema["required"])
    assert {
        "ROLE_ARN",
        "AWS_REGION",
        "ECS_CLUSTER",
        "ECS_SERVICE",
        "ECR_REPOSITORY",
        "CLAMD_ECR_REPOSITORY",
        "API_IMAGE_DIGEST",
        "CLAMD_IMAGE_DIGEST",
        "ECS_EXECUTION_ROLE_ARN",
        "ECS_TASK_ROLE_ARN",
        "QUALIFIED_ECR_IMAGE_URI_BY_DIGEST",
        "QUALIFIED_CLAMD_IMAGE_URI_BY_DIGEST",
        "OPERATIONS_AUTH_TOKEN_SECRET_REFERENCE",
        "API_BASE_URL",
    } <= required
    assert "OPERATIONS_AUTH_TOKEN" not in schema["properties"]


def test_activation_runbook_preserves_security_cost_and_live_nonclaims() -> None:
    text = (
        ROOT / "docs" / "runbooks" / "AWS_PILOT_ACTIVATION_READINESS.md"
    ).read_text(encoding="utf-8")
    for marker in (
        "GitHub deployment / qualification identity",
        "ECS task execution role",
        "Application runtime role",
        "desiredCount=1",
        "Do not add S3 lifecycle/retention rules",
        "same Fargate task",
        "DOCUMENT_EXTRACTION_PROVIDER=aws_textract",
        "PRODUCTION SCANNER DEPLOYMENT NOT_RUN",
        "LIVE AWS PILOT NOT_RUN",
    ):
        assert marker in text
    assert "AdministratorAccess" in text
    assert "Do not fabricate any of them" in text
