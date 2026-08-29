from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import subprocess

import pytest

from scripts import generate_pilot_deployment_values as generator


def _args(tmp_path: Path) -> Namespace:
    return Namespace(
        profile="admin",
        storage_profile="storage",
        region="ap-south-1",
        repository_name="repo",
        image_tag="tag",
        execution_role_name="execution",
        task_role_name="task",
        log_group_name="logs",
        bucket="bucket",
        runtime_secret_id="runtime",
        storage_secret_id="storage",
        envelope_kms_key_id="envelope",
        storage_kms_key_id="storage",
        output=tmp_path / "values.json",
    )


def test_rejects_repo_output(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.output = generator.PROJECT_ROOT / "bad.json"
    with pytest.raises(ValueError):
        generator.generate(args)


def test_generates_strings_from_metadata_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake(profile, region, *command):
        calls.append(command)
        action = command[1]
        if action == "get-role":
            return {"Role": {"Arn": "synthetic-role"}}
        if action == "describe-images":
            return {
                "imageDetails": [
                    {"imageDigest": "sha256:abc", "registryId": "synthetic"}
                ]
            }
        if action == "get-bucket-location":
            return {"LocationConstraint": region}
        if action == "describe-key":
            return {
                "KeyMetadata": {"KeyState": "Enabled", "KeyUsage": "ENCRYPT_DECRYPT"}
            }
        return {}

    monkeypatch.setattr(generator, "_aws", fake)
    values = generator.generate(_args(tmp_path))
    assert all(isinstance(value, str) for value in values.values())
    assert "@sha256:" in values["QUALIFIED_ECR_IMAGE_URI_BY_DIGEST"]
    assert {
        (command[0], command[1]) for command in calls
    } == generator.APPROVED_AWS_COMMANDS


@pytest.mark.parametrize(
    "response",
    [
        {"LocationConstraint": "wrong"},
        {"imageDetails": []},
        {"KeyMetadata": {"KeyState": "Disabled", "KeyUsage": "ENCRYPT_DECRYPT"}},
    ],
)
def test_bad_metadata_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: dict
) -> None:
    monkeypatch.setattr(generator, "_aws", lambda *_args: response)
    with pytest.raises(RuntimeError):
        generator.generate(_args(tmp_path))


def test_every_approved_command_reaches_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(generator.subprocess, "run", fake_run)
    for service, action in generator.APPROVED_AWS_COMMANDS:
        assert generator._aws("profile", "ap-south-1", service, action) == {}
    assert {(call[5], call[6]) for call in calls} == generator.APPROVED_AWS_COMMANDS


@pytest.mark.parametrize(
    "command",
    [
        ("iam", "put-role-policy"),
        ("iam", "create-role"),
        ("ecs", "run-task"),
        ("ecs", "register-task-definition"),
        ("secretsmanager", "get-secret-value"),
        ("secretsmanager", "put-secret-value"),
        ("s3api", "put-bucket-lifecycle-configuration"),
        ("kms", "schedule-key-deletion"),
        ("iam", "get-roel"),
        ("unknown", "describe"),
        ("iam",),
        (),
    ],
)
def test_disallowed_commands_are_rejected_before_subprocess(
    monkeypatch: pytest.MonkeyPatch, command: tuple[str, ...]
) -> None:
    monkeypatch.setattr(
        generator.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("subprocess must not be called"),
    )
    with pytest.raises(ValueError):
        generator._aws("profile", "ap-south-1", *command)


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess([], 1, stdout="", stderr="AccessDenied"),
        subprocess.CompletedProcess([], 0, stdout="not-json", stderr=""),
    ],
)
def test_subprocess_errors_fail_closed(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str]
) -> None:
    monkeypatch.setattr(generator.subprocess, "run", lambda *_args, **_kwargs: result)
    with pytest.raises(RuntimeError):
        generator._aws("profile", "ap-south-1", "sts", "get-caller-identity")


def test_subprocess_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("aws", 30)

    monkeypatch.setattr(generator.subprocess, "run", timeout)
    with pytest.raises(RuntimeError):
        generator._aws("profile", "ap-south-1", "sts", "get-caller-identity")
