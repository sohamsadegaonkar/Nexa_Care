import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import _trusted_hosts, app


ROOT = Path(__file__).resolve().parents[1]


def test_trusted_host_parsing_trims_whitespace() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_trusted_hosts"
            for target in node.targets
        )
    )
    assert "strip" in ast.unparse(assignment)
    assert _trusted_hosts == ["localhost", "127.0.0.1", "testserver"]


def test_health_accepts_testserver_and_rejects_untrusted_host() -> None:
    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    connection = AsyncMock()
    connection.execute = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = connection
    engine = MagicMock()
    engine.connect.return_value = context
    db_session = MagicMock()
    db_context = AsyncMock()
    db_context.__aenter__.return_value = db_session
    db_context.__aexit__.return_value = False
    session_factory = MagicMock(return_value=db_context)
    outbox_health = AsyncMock(
        return_value={
            "pending_count": 0,
            "dead_letter_backlog": 0,
            "expired_lease_count": 0,
            "oldest_pending_age_seconds": 0.0,
            "oldest_expired_lease_age_seconds": 0.0,
        }
    )
    running_task = MagicMock()
    running_task.done.return_value = False
    app.state.audit_outbox_task = running_task
    with (
        patch("app.main.get_async_redis_client", return_value=redis),
        patch("app.main.get_async_engine", return_value=engine),
        patch("app.main.get_session_factory", return_value=session_factory),
        patch("app.main.get_outbox_health", new=outbox_health),
    ):
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        assert session_factory.called
        outbox_health.assert_awaited_with(db_session)
        assert (
            client.get("/health", headers={"host": "untrusted.invalid"}).status_code
            == 400
        )


def test_preflight_uses_explicit_allowed_local_host() -> None:
    source = (ROOT / "scripts" / "consent_preflight.py").read_text(encoding="utf-8")
    assert 'TestClient(app, base_url="http://localhost")' in source


def test_production_default_is_not_testserver() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'os.getenv("TRUSTED_HOSTS", "*")' in source
    assert 'os.getenv("TRUSTED_HOSTS", "testserver")' not in source
