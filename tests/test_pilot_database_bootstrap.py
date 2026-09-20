from __future__ import annotations

import asyncio
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


class FakeTransaction:
    def __init__(self, connection: "FakeConnection"):
        self.connection = connection

    async def __aenter__(self):
        self.connection.transaction_enters += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.connection.transaction_commits += 1
        else:
            self.connection.transaction_rollbacks += 1
        return False


class FakeConnection:
    def __init__(
        self,
        *,
        role_states: dict[str, dict] | None = None,
        memberships: dict[str, list[str]] | None = None,
        migration_heads: list[str] | None = None,
        migration_query_error: bool = False,
        fail_execute_contains: str | None = None,
    ):
        self.role_states = role_states or {}
        self.memberships = memberships or {}
        self.migration_heads = migration_heads
        self.migration_query_error = migration_query_error
        self.fail_execute_contains = fail_execute_contains
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.executed: list[str] = []
        self.closed = False
        self.transaction_enters = 0
        self.transaction_commits = 0
        self.transaction_rollbacks = 0

    def transaction(self):
        return FakeTransaction(self)

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        assert query == bootstrap.ROLE_STATE_SQL
        return self.role_states.get(args[0])

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        if query == bootstrap.ROLE_MEMBERSHIP_SQL:
            return [{"rolname": name} for name in self.memberships.get(args[0], [])]
        if query == bootstrap.MIGRATION_HEAD_SQL:
            if self.migration_query_error:
                raise RuntimeError("missing table")
            return [
                {"version_num": head}
                for head in (
                    self.migration_heads
                    if self.migration_heads is not None
                    else []
                )
            ]
        raise AssertionError(query)

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query, args))
        if query.startswith("SELECT format("):
            return "<password-bearing-ddl>"
        raise AssertionError(query)

    async def execute(self, query: str):
        if self.fail_execute_contains and self.fail_execute_contains in query:
            raise RuntimeError("forced failure")
        self.executed.append(query)
        return "OK"

    async def close(self):
        self.closed = True


def hardened_role_state(role_name: str, **overrides):
    state = {
        "rolname": role_name,
        "rolcanlogin": True,
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolinherit": False,
        "rolreplication": False,
        "rolbypassrls": False,
    }
    state.update(overrides)
    return state


def role_payload(username: str, password: str = "persisted!p@ss/word") -> str:
    cred = bootstrap.DatabaseCredential(username, password, "db.example.test", 5432)
    return bootstrap.credential_secret_payload(cred)


def credential_pair() -> bootstrap.CredentialPair:
    return bootstrap.CredentialPair(
        migrator=bootstrap.DatabaseCredential(
            bootstrap.MIGRATOR_ROLE_NAME, "m-pass", "db.example.test", 5432
        ),
        runtime=bootstrap.DatabaseCredential(
            bootstrap.RUNTIME_ROLE_NAME, "r-pass", "db.example.test", 5432
        ),
        source="reused",
    )


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


@pytest.mark.parametrize(
    "role_name",
    [bootstrap.MIGRATOR_ROLE_NAME, bootstrap.RUNTIME_ROLE_NAME],
)
def test_new_roles_receive_explicit_security_attributes(role_name):
    connection = FakeConnection()
    asyncio.run(bootstrap.ensure_fixed_login_role(connection, role_name, "safe-password"))
    format_query, args = connection.fetchval_calls[0]
    assert "CREATE ROLE" in format_query
    for attribute in (
        "LOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert attribute in format_query
    assert "%L" in format_query
    assert "$1" in format_query
    assert args == ("safe-password",)


def test_existing_elevated_role_is_inspected_and_normalized():
    role_name = bootstrap.MIGRATOR_ROLE_NAME
    connection = FakeConnection(
        role_states={
            role_name: hardened_role_state(
                role_name,
                rolsuper=True,
                rolcreatedb=True,
                rolcreaterole=True,
                rolinherit=True,
                rolreplication=True,
                rolbypassrls=True,
            )
        }
    )
    asyncio.run(bootstrap.ensure_fixed_login_role(connection, role_name, "normalized"))
    assert connection.fetchrow_calls == [(bootstrap.ROLE_STATE_SQL, (role_name,))]
    assert connection.fetch_calls == [(bootstrap.ROLE_MEMBERSHIP_SQL, (role_name,))]
    format_query, args = connection.fetchval_calls[0]
    assert "ALTER ROLE" in format_query
    assert args == ("normalized",)
    for attribute in (
        "LOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert attribute in format_query


def test_unexpected_membership_fails_closed_before_alter():
    role_name = bootstrap.RUNTIME_ROLE_NAME
    connection = FakeConnection(
        role_states={role_name: hardened_role_state(role_name)},
        memberships={role_name: ["unexpected_admin_role"]},
    )
    with pytest.raises(bootstrap.BootstrapError) as exc:
        asyncio.run(bootstrap.ensure_fixed_login_role(connection, role_name, "password"))
    assert exc.value.code is bootstrap.FailureCode.ROLE_MEMBERSHIP_INVALID
    assert connection.fetchval_calls == []
    assert connection.executed == []


def test_role_names_are_fixed():
    connection = FakeConnection()
    with pytest.raises(bootstrap.BootstrapError):
        asyncio.run(
            bootstrap.ensure_fixed_login_role(
                connection, "attacker_role", "password"
            )
        )


def test_privilege_matrix_has_required_separation():
    joined = "\n".join(bootstrap.MASTER_GRANT_STATEMENTS)
    assert "GRANT CONNECT, CREATE ON DATABASE nexacare_pilot TO nexa_migrator" in joined
    assert "GRANT USAGE, CREATE ON SCHEMA public TO nexa_migrator" in joined
    assert "GRANT CONNECT ON DATABASE nexacare_pilot TO nexa_api_runtime" in joined
    assert "GRANT USAGE ON SCHEMA public TO nexa_api_runtime" in joined
    assert "REVOKE CREATE ON SCHEMA public FROM nexa_api_runtime" in joined
    assert "CREATE DATABASE" not in joined
    assert "rds_superuser" not in joined


def test_default_privileges_are_exact_and_no_sequence_update():
    joined = "\n".join(bootstrap.MIGRATOR_DEFAULT_PRIVILEGE_STATEMENTS)
    assert "REVOKE ALL ON TABLES FROM nexa_api_runtime" in joined
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexa_api_runtime" in joined
    assert "REVOKE ALL ON SEQUENCES FROM nexa_api_runtime" in joined
    assert "GRANT USAGE ON SEQUENCES TO nexa_api_runtime" in joined
    assert "GRANT UPDATE ON SEQUENCES" not in joined


def test_master_mutation_is_transactional_and_rolls_back_on_failure():
    pair = credential_pair()
    connection = FakeConnection(
        fail_execute_contains="GRANT CONNECT, CREATE ON DATABASE"
    )
    with pytest.raises(bootstrap.BootstrapError) as exc:
        asyncio.run(bootstrap.run_master_phase(connection, pair))
    assert exc.value.code is bootstrap.FailureCode.DB_MUTATION_FAILED
    assert connection.transaction_enters == 1
    assert connection.transaction_commits == 0
    assert connection.transaction_rollbacks == 1


def test_master_transaction_rolls_back_if_second_role_has_membership():
    pair = credential_pair()
    connection = FakeConnection(
        role_states={
            bootstrap.MIGRATOR_ROLE_NAME: hardened_role_state(
                bootstrap.MIGRATOR_ROLE_NAME
            ),
            bootstrap.RUNTIME_ROLE_NAME: hardened_role_state(
                bootstrap.RUNTIME_ROLE_NAME
            ),
        },
        memberships={bootstrap.RUNTIME_ROLE_NAME: ["unexpected_role"]},
    )
    with pytest.raises(bootstrap.BootstrapError) as exc:
        asyncio.run(bootstrap.run_master_phase(connection, pair))
    assert exc.value.code is bootstrap.FailureCode.ROLE_MEMBERSHIP_INVALID
    assert connection.transaction_commits == 0
    assert connection.transaction_rollbacks == 1


def test_migrator_default_privilege_phase_is_transactional():
    connection = FakeConnection(fail_execute_contains="GRANT USAGE ON SEQUENCES")
    with pytest.raises(bootstrap.BootstrapError) as exc:
        asyncio.run(bootstrap.run_migrator_default_privilege_phase(connection))
    assert exc.value.code is bootstrap.FailureCode.DEFAULT_PRIVILEGES_FAILED
    assert connection.transaction_enters == 1
    assert connection.transaction_commits == 0
    assert connection.transaction_rollbacks == 1


def test_exact_migration_head_allows_atomic_post_migration_grants():
    connection = FakeConnection(
        migration_heads=[bootstrap.EXPECTED_MIGRATION_HEAD]
    )
    asyncio.run(bootstrap.run_post_migration_phase(connection))
    assert connection.fetch_calls[0] == (bootstrap.MIGRATION_HEAD_SQL, ())
    assert connection.executed == list(bootstrap.POST_MIGRATION_STATEMENTS)
    assert connection.transaction_commits == 1
    assert connection.transaction_rollbacks == 0


@pytest.mark.parametrize(
    "heads,query_error",
    [
        ([], False),
        (["wrong_head"], False),
        (
            [
                bootstrap.EXPECTED_MIGRATION_HEAD,
                bootstrap.EXPECTED_MIGRATION_HEAD,
            ],
            False,
        ),
        (None, True),
    ],
)
def test_wrong_missing_or_multiple_migration_head_fails_closed(heads, query_error):
    connection = FakeConnection(
        migration_heads=heads,
        migration_query_error=query_error,
    )
    with pytest.raises(bootstrap.BootstrapError) as exc:
        asyncio.run(bootstrap.run_post_migration_phase(connection))
    assert exc.value.code is bootstrap.FailureCode.MIGRATION_HEAD_MISMATCH
    assert connection.executed == []
    assert connection.transaction_commits == 0
    assert connection.transaction_rollbacks == 1


def test_post_migration_privilege_failure_rolls_back_atomically():
    connection = FakeConnection(
        migration_heads=[bootstrap.EXPECTED_MIGRATION_HEAD],
        fail_execute_contains="GRANT USAGE ON ALL SEQUENCES",
    )
    with pytest.raises(bootstrap.BootstrapError) as exc:
        asyncio.run(bootstrap.run_post_migration_phase(connection))
    assert exc.value.code is bootstrap.FailureCode.POST_MIGRATION_FAILED
    assert connection.transaction_commits == 0
    assert connection.transaction_rollbacks == 1


def test_post_migration_privilege_contract_is_narrow():
    statements = "\n".join(bootstrap.POST_MIGRATION_STATEMENTS)
    assert "REVOKE ALL PRIVILEGES ON ALL TABLES" in statements
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in statements
    assert "REVOKE ALL PRIVILEGES ON ALL SEQUENCES" in statements
    assert "GRANT USAGE ON ALL SEQUENCES" in statements
    assert "GRANT SELECT ON TABLE public.alembic_version" in statements
    assert (
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.alembic_version"
        in statements
    )
    assert "REVOKE CREATE ON SCHEMA public FROM nexa_api_runtime" in statements
    assert "GRANT TRUNCATE" not in statements
    assert "GRANT REFERENCES" not in statements
    assert "GRANT TRIGGER" not in statements
    assert "GRANT CREATE" not in statements
    assert "GRANT ALTER" not in statements
    assert "GRANT DROP" not in statements
    assert "GRANT UPDATE ON ALL SEQUENCES" not in statements


def test_bootstrap_uses_transactions_and_second_connection_as_migrator(monkeypatch):
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
            bootstrap.MIGRATOR_SECRET_ID: role_payload(
                bootstrap.MIGRATOR_ROLE_NAME, "m-pass"
            ),
            bootstrap.RUNTIME_SECRET_ID: role_payload(
                bootstrap.RUNTIME_ROLE_NAME, "r-pass"
            ),
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
    assert connections[0][1].transaction_commits == 1
    assert connections[1][1].transaction_commits == 1
    assert list(connections[1][1].executed) == list(
        bootstrap.MIGRATOR_DEFAULT_PRIVILEGE_STATEMENTS
    )


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


def test_ecs_task_trust_uses_supported_source_arn_and_retains_source_account():
    root = Path(__file__).resolve().parents[1]
    trust = json.loads(
        (
            root
            / "deploy/iam/pilot-database-bootstrap-role-trust.template.json"
        ).read_text()
    )
    statement = trust["Statement"][0]
    assert statement["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
    condition = statement["Condition"]
    assert condition["StringEquals"]["aws:SourceAccount"] == "654654144224"
    assert condition["ArnLike"]["aws:SourceArn"] == (
        "arn:aws:ecs:ap-south-1:654654144224:*"
    )


def test_future_run_task_caller_is_exact_cluster_and_task_revision_scoped():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads(
        (
            root
            / "deploy/iam/pilot-database-bootstrap-run-task-policy.template.json"
        ).read_text()
    )
    statement = policy["Statement"][0]
    assert statement["Action"] == "ecs:RunTask"
    assert statement["Resource"] == (
        "arn:aws:ecs:ap-south-1:654654144224:task-definition/"
        "nexa-care-pilot-database-bootstrap:<BOOTSTRAP_TASK_DEFINITION_REVISION>"
    )
    assert statement["Condition"]["ArnEquals"]["ecs:cluster"] == (
        "arn:aws:ecs:ap-south-1:654654144224:cluster/nexa-care-pilot"
    )
    assert not statement["Resource"].endswith(":*")


def test_runbook_documents_ecs_trust_limitation_and_partial_recovery_gate():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs/runbooks/PILOT_DATABASE_BOOTSTRAP.md").read_text()
    assert "does not currently support using `aws:SourceArn`" in text
    assert "nexa-care-pilot" in text
    assert "exact rendered revision" in text
    assert "separately authorized recovery procedure" in text


def test_iam_templates_are_narrowly_scoped():
    root = Path(__file__).resolve().parents[1]
    execution = json.loads(
        (
            root
            / "deploy/iam/pilot-database-bootstrap-execution-policy.template.json"
        ).read_text()
    )
    task = json.loads(
        (
            root
            / "deploy/iam/pilot-database-bootstrap-task-policy.template.json"
        ).read_text()
    )
    exec_text = json.dumps(execution)
    task_text = json.dumps(task)
    assert "secretsmanager" not in exec_text
    assert "kms:" not in task_text
    assert "s3:" not in task_text
    assert "textract:" not in task_text
    assert "iam:" not in task_text
    assert "secretsmanager:*" not in task_text
    assert task["Statement"][0]["Resource"] == "<RDS_MANAGED_MASTER_SECRET_ARN>"
    resources = task["Statement"][1]["Resource"]
    assert resources == [
        "arn:aws:secretsmanager:ap-south-1:654654144224:secret:nexa-care/pilot/db/runtime-*",
        "arn:aws:secretsmanager:ap-south-1:654654144224:secret:nexa-care/pilot/db/migrator-*",
    ]
