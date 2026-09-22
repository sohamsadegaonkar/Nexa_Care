from __future__ import annotations

from pathlib import Path


SHIM = Path(__file__).resolve().parents[1] / "scripts" / "metro_bind_host.cjs"


def test_metro_bind_shim_is_port_scoped_and_forces_validated_host():
    source = SHIM.read_text(encoding="utf-8")

    assert "NEXA_METRO_BIND_HOST" in source
    assert "NEXA_METRO_BIND_PORT" in source
    assert "first.port === bindPort" in source
    assert "net.Server.prototype.listen" in source
    assert "originalListen.apply(this, args)" in source
    assert "host: bindHost" in source
