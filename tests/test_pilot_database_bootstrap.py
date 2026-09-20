from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import ssl
import sys
import types

import pytest

from scripts import bootstrap_pilot_database as bootstrap


class FakeSecrets:
    def __init__(self, initial: dict[str, str | None]):
        self.values = dict(initial)
        self.put_calls: list[tuple[str, str]] = []

    def describe_secret(self, *, SecretId: str):
        value = self.values.get(SecretId)
        return {"VersionIdsToStages": {} if value is None else {"v1": ["AWSCURRENT"]}}

    def get_secret_value(self, *, SecretId: str, VersionStage: str):
        assert VersionStage == "AWSCURRENT"
        value = self.values.get(SecretId)
        if value is None:
            raise RuntimeError("no current value")
        return {"SecretString": value}

    def put_secret_value(self, *, SecretId: str, SecretString: str, VersionStages: list[str]):
        assert VersionStages == ["AWSCURRENT"]
        self.values[SecretId] = SecretString
        self.put_calls.append((SecretId, SecretString))
        return {"VersionId": "new"}


class FakeConnection:
    def __init__(self, existing: set[str] | None = None):
        self.existing = existing or set()
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.executed: list[str] = []
        self.closed = False

    async def fetchval(self, query: str, *args):
        self.fetch_calls.append((query, args))
        if query == bootstrap.ROLE_EXISTS_SQL:
            return args[0] in self.existing
        if query.startswith("SELECT format("):
            return "<password-bearing-ddl>"
        raise AssertionError(query)

    async def execute(self, query: str):
        self.executed.append(query)
        return "OK"

    async def close(self):
        self.closed = True


def role_payload(username: str, password: str = "persisted!p@ss/word") -> str:
    cred = bootstrap.DatabaseCredential(username, password, "db.example.test", 5432)
    return bootstrap.credential_secret_payload(cred)


def test_empty_empty_generates_persists_then_rereads(monkeypatch):
    client = FakeSecrets({bootstrap.RUNTIME_SECRET_ID: None, bootstrap.MIGRATOR_SECRET_ID: None})
    passwords = iter(["migrator-new", "runtime-new"])
    monkeypatch.setattr(bootstrap, "generate_password", lambda: next(passwords))
    pair = bootstrap.resolve_role_credentials(client, host="db.example.test", port=5432)
    assert pair.source == "generated"
    assert pair.migrator.password == "migrator-new"
    assert pair.runtime.password == "runtime-new"
    assert [call[0] for call in client.put_calls] == [
        bootstrap.MIGRATOR_SECRET_ID,
        bootstrap.RUNTIME_SECRET_ID,
    ]


def test_populated_populated_reuses_without_rotation(monkeypatch):
    client = FakeSecrets(
        {
            bootstrap.MIGRATOR_SECRET_ID: role_payload(bootstrap.MIGRATOR_ROLE_NAME, "keep-m"),
            bootstrap.RUNTIME_SECRET_ID: role_payload(bootstrap.RUNTIME_ROLE_NAME, "keep-r"),
        }
    )
    monkeypatch.setattr(bootstrap, "generate_password", lambda: pytest.fail("must not rotate"))
    pair = bootstrap.resolve_role_credentials(client, host="db.example.test", port=5432)
    assert pair.source == "reused"
    assert pair.migrator.password == "keep-m"
    assert pair.runtime.password == "keep-r"
    assert client.put_calls == []


def test_partial_state_fails_closed():
    client = FakeSecrets(
        {
            bootstrap.MIGRATOR_SECRET_ID: role_payload(bootstrap.MIGRATOR_ROLE_NAME),
            bootstrap.RUNTIME_SECRET_ID: None,
        }
    )
    with pytest.raises(bootstrap.BootstrapError) as exc:
        bootstrap.resolve_role_credentials(client, host="db.example.test", port=5432)
    assert exc.value.code is bootstrap.FailureCode.PARTIAL_SECRET_STATE


def test_ambiguous_noncurrent_secret_state_fails_closed():
    class Ambiguous(FakeSecrets):
        def describe_secret(self, *, SecretId: str):
            return {"VersionIdsToStages": {"old": ["AWSPREVIOUS"]}}

    with pytest.raises(bootstrap.BootstrapError) as exc:
        bootstrap._secret_state(Ambiguous({}), bootstrap.RUNTIME_SECRET_ID)
    assert exc.value.code is bootstrap.FailureCode.SECRET_STATE_INVALID


def test_database_url_percent_encodes_credentials():
    cred = bootstrap.DatabaseCredential(
        "user+name", "p@ss:/?#[]% word", "db.example.test", 5432
    )
    url = bootstrap.build_database_url(cred)
    assert "user%2Bname" in url
    assert "p%40ss%3A%2F%3F%23%5B%5D%25%20word" in url
    assert "p@ss" not in url


def test_role_names_are_fixed_and_password_is_bound_not_interpolated():
    connection = FakeConnection()
    password = "dangerous'password;DROP ROLE x;--"
    asyncio.run(
        bootstrap.ensure_fixed_login_role(
            connection, bootstrap.MIGRATOR_ROLE_NAME, password
        )
    )
    format_query, args = connection.fetch_calls[1]
    assert "%L" in format_query
    assert "$1" in format_query
    assert password not in format_query
    assert args == (password,)
    with pytest.raises(bootstrap.BootstrapError):
        asyncio.run(bootstrap.ensure_fixed_login_role(connection, "attacker_role", password))


def test_privilege_matrix_has_required_separation():
    joined = "\n".join(bootstrap.MASTER_GRANT_STATEMENTS)
    assert "GRANT CONNECT, CREATE ON DATABASE nexacare_pilot TO nexa_migrator" in joined
    assert "GRANT USAGE, CREATE ON SCHEMA public TO nexa_migrator" in joined
    assert "GRANT CONNECT ON DATABASE nexacare_pilot TO nexa_api_runtime" in joined
    assert "GRANT USAGE ON SCHEMA public TO nexa_api_runtime" in joined
    assert "REVOKE CREATE ON SCHEMA public FROM nexa_api_runtime" in joined
    assert "CREATE DATABASE" not in joined
    assert "rds_superuser" not in joined


def test_default_privileges_are_migrator_owned_and_no_sequence_update():
    table_stmt, sequence_stmt = bootstrap.MIGRATOR_DEFAULT_PRIVILEGE_STATEMENTS
    assert table_stmt.startswith("ALTER DEFAULT PRIVILEGES IN SCHEMA public")
    assert "SELECT, INSERT, UPDATE, DELETE ON TABLES" in table_stmt
    assert "GRANT USAGE ON SEQUENCES" in sequence_stmt
    assert "UPDATE" not in sequence_stmt


def test_post_migration_hardens_alembic_version_and_sequences():
    joined = "\n".join(bootstrap.POST_MIGRATION_STATEMENTS)
    assert "REVOKE UPDATE ON ALL SEQUENCES" in joined
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.alembic_version" in joined
    assert "GRANT SELECT ON TABLE public.alembic_version" in joined


def test_bootstrap_uses_second_connection_as_migrator(monkeypatch):
    master_payload = json.dumps(
        {
            "username": bootstrap.MASTER_ROLE_NAME,
            "password": "master-secret",
            "engine": "postgres",
            "host": "db.example.test",
            "port": 5432,
            "dbname": bootstrap.DATABASE_NAME,
        }
    )
    client = FakeSecrets(
        {
            "master": master_payload,
            bootstrap.MIGRATOR_SECRET_ID: role_payload(bootstrap.MIGRATOR_ROLE_NAME, "m-pass"),
            bootstrap.RUNTIME_SECRET_ID: role_payload(bootstrap.RUNTIME_ROLE_NAME, "r-pass"),
        }
    )
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("DATABASE_ECHO_SQL", "false")
    monkeypatch.setenv("NEXA_DB_MASTER_SECRET_ID", "master")
    connections: list[tuple[str, FakeConnection]] = []

    async def fake_connect(credential):
        conn = FakeConnection()
        connections.append((credential.username, conn))
        return conn

    monkeypatch.setattr(bootstrap, "connect_database", fake_connect)
    pair = asyncio.run(bootstrap.bootstrap(client))
    assert pair.source == "reused"
    assert [name for name, _ in connections] == [
        bootstrap.MASTER_ROLE_NAME,
        bootstrap.MIGRATOR_ROLE_NAME,
    ]
    assert list(connections[1][1].executed) == list(bootstrap.MIGRATOR_DEFAULT_PRIVILEGE_STATEMENTS)


def test_strict_tls_uses_shared_database_contract(monkeypatch):
    called = []
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    app_mod = types.ModuleType("app")
    core_mod = types.ModuleType("app.core")
    db_mod = types.ModuleType("app.core.database")

    def fake_create(path):
        called.append(path)
        return context

    db_mod.create_database_ssl_context = fake_create
    monkeypatch.setitem(sys.modules, "app", app_mod)
    monkeypatch.setitem(sys.modules, "app.core", core_mod)
    monkeypatch.setitem(sys.modules, "app.core.database", db_mod)
    assert bootstrap.build_shared_tls_context() is context
    assert called == [bootstrap.DATABASE_SSL_CA_PATH]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_failure_output_is_redacted(capsys):
    secret = "super-secret-password"
    dsn = "postgresql+asyncpg://user:secret@host/db"
    err = bootstrap.BootstrapError(bootstrap.FailureCode.DB_MUTATION_FAILED)
    bootstrap._emit_failure(err.code)
    output = capsys.readouterr().out
    assert secret not in output
    assert dsn not in output
    assert output.strip() == "ERROR: BOOTSTRAP_DB_MUTATION_FAILED"


def test_task_template_has_no_plaintext_secret_or_ports():
    root = Path(__file__).resolve().parents[1]
    task = json.loads(
        (
            root
            / "deploy/ecs/nexa-care-pilot-database-bootstrap-task-definition.template.json"
        ).read_text()
    )
    assert task["networkMode"] == "awsvpc"
    assert task["requiresCompatibilities"] == ["FARGATE"]
    container = task["containerDefinitions"][0]
    assert "portMappings" not in container
    assert "secrets" not in container
    env = {item["name"]: item["value"] for item in container["environment"]}
    assert env["AWS_REGION"] == "ap-south-1"
    assert env["DATABASE_SSL_CA_PATH"] == "/app/deploy/ssl/aws-rds-ca-bundle.pem"
    assert container["image"] == "<QUALIFIED_ECR_IMAGE_URI_BY_DIGEST>"
    serialized = json.dumps(task)
    assert "password" not in serialized.lower()
    assert "DATABASE_URL" not in serialized


def test_iam_templates_are_narrowly_scoped():
    root = Path(__file__).resolve().parents[1]
    execution = json.loads(
        (root / "deploy/iam/pilot-database-bootstrap-execution-policy.template.json").read_text()
    )
    task = json.loads(
        (root / "deploy/iam/pilot-database-bootstrap-task-policy.template.json").read_text()
    )
    trust = json.loads(
        (root / "deploy/iam/pilot-database-bootstrap-role-trust.template.json").read_text()
    )
    exec_text = json.dumps(execution)
    task_text = json.dumps(task)
    assert "secretsmanager" not in exec_text
    assert "kms:" not in task_text
    assert "s3:" not in task_text
    assert "textract:" not in task_text
    assert "iam:" not in task_text
    assert "secretsmanager:*" not in task_text
    resources = task["Statement"][1]["Resource"]
    assert resources == [
        "arn:aws:secretsmanager:ap-south-1:654654144224:secret:nexa-care/pilot/db/runtime-*",
        "arn:aws:secretsmanager:ap-south-1:654654144224:secret:nexa-care/pilot/db/migrator-*",
    ]
    condition = trust["Statement"][0]["Condition"]
    assert condition["StringEquals"]["aws:SourceAccount"] == "654654144224"
    assert condition["ArnLike"]["aws:SourceArn"] == (
        "arn:aws:ecs:ap-south-1:654654144224:task/nexa-care-pilot/*"
    )
