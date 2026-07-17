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
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_trusted_hosts" for target in node.targets)
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
    with patch("app.main.get_async_redis_client", return_value=redis), patch(
        "app.main.get_async_engine", return_value=engine
    ):
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        assert client.get("/health", headers={"host": "untrusted.invalid"}).status_code == 400


def test_preflight_uses_explicit_allowed_local_host() -> None:
    source = (ROOT / "scripts" / "consent_preflight.py").read_text(encoding="utf-8")
    assert 'TestClient(app, base_url="http://localhost")' in source


def test_production_default_is_not_testserver() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'os.getenv("TRUSTED_HOSTS", "*")' in source
    assert 'os.getenv("TRUSTED_HOSTS", "testserver")' not in source
