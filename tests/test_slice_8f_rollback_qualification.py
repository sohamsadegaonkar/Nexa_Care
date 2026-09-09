from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rollback-runtime-qualification.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_rollback_gate_requires_explicit_pilot_targets_and_oidc() -> None:
    text = _workflow()
    assert "environment: pilot" in text
    assert "id-token: write" in text
    assert "NEXA_PILOT_AWS_ROLE_ARN" in text
    assert "NEXA_PILOT_ROLLBACK_TASK_DEFINITION" in text
    assert "BLOCKED_MISSING_ACCOUNT_WIRING" in text
    assert "aws-actions/configure-aws-credentials@v4" in text


def test_rollback_candidate_is_immutable_and_runs_as_one_off_fargate_task() -> None:
    text = _workflow()
    assert "@sha256:" in text
    assert "aws ecr describe-images" in text
    assert "aws ecs run-task" in text
    assert "--launch-type FARGATE" in text
    assert "--count 1" in text
    assert "run_production_startup_preflight" in text
    assert "aws ecs wait tasks-stopped" in text
    assert "ROLLBACK_CANDIDATE_RUNTIME_PREFLIGHT=PASS" in text


def test_rollback_gate_never_switches_service_or_mutates_schema() -> None:
    text = _workflow()
    forbidden = (
        "aws ecs update-service",
        "alembic downgrade",
        "run_pilot_migrations.py",
        "alembic upgrade",
    )
    for command in forbidden:
        assert command not in text
    assert "ROLLBACK_TRAFFIC_SWITCH=NOT_PERFORMED" in text
    assert "DATABASE_DOWNGRADE=NOT_PERFORMED" in text


def test_rollback_gate_rechecks_current_monitoring_surfaces() -> None:
    text = _workflow()
    for surface in ("/healthz", "/health", "/ops/health", "/metrics"):
        assert surface in text
    assert "X-Nexa-Operations-Token" in text
    assert "CURRENT_SERVICE_MONITORING=PASS" in text


def test_rollback_candidate_rejects_static_aws_credentials() -> None:
    text = _workflow()
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        assert name in text
    assert "rollback candidate contains static AWS credential variables" in text
