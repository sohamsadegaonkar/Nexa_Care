from __future__ import annotations

from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "start_demo_dev.ps1"


def test_default_demo_backend_is_loopback_only_and_physical_bind_is_explicit():
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "function Test-PrivateIpv4Cidr" in source
    assert "($octets[0] -eq 10 -and $prefixLength -ge 8)" in source
    assert "$backendBindHost = if ($usesBuiltInMobileTransport) { '127.0.0.1' } else { $mobileHost }" in source
    assert "$expoHostMode = if ($usesBuiltInMobileTransport) { 'localhost' } else { 'lan' }" in source
    assert "workspace expo-app start --host $expoHostMode --port $MetroPort" in source
    assert "NEXA_METRO_BIND_HOST = $expoAdvertisedHost" in source
    assert "NEXA_METRO_BIND_PORT = [string]$MetroPort" in source
    assert "metro_bind_host.cjs" in source
    assert "'-m', 'uvicorn', 'app.main:app', '--host', $backendBindHost" in source
    assert 'API_PROXY_TARGET = "http://${backendBindHost}:$BackendPort"' in source
    assert "'--host', '0.0.0.0'" not in source


def test_powershell_syntax_is_valid():
    import shutil
    import subprocess

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return
    ps_cmd = (
        "$errors = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{LAUNCHER}', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors) { foreach ($err in $errors) { [Console]::Error.WriteLine($err.ToString()) }; exit 1 } else { exit 0 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"PowerShell parse errors:\n{result.stderr}"
