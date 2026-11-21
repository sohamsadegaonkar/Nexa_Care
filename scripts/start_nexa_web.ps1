[CmdletBinding()]
param(
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$clientRoot = Join-Path $repoRoot 'nexa-client'

function Resolve-RepoPython {
    $candidates = @()
    if ($env:VIRTUAL_ENV) {
        $candidates += (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe')
    }
    $candidates += (Join-Path $repoRoot '.venv\Scripts\python.exe')
    $candidates += (Join-Path $repoRoot 'venv\Scripts\python.exe')
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw 'Python virtual environment missing; create .venv before local development.'
}

function Assert-FreeLocalPort {
    param([Parameter(Mandatory)][int]$Port)
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        throw "Port $Port is already in use. Stop the existing service manually; this launcher never kills processes."
    }
}

function Start-LocalWindow {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$Body
    )
    $script = @"
`$Host.UI.RawUI.WindowTitle = '$Title'
`$ErrorActionPreference = 'Stop'
Set-Location '$WorkingDirectory'
$Body
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoLogo', '-NoExit', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
    ) | Out-Null
}

$python = Resolve-RepoPython
$corepackCommand = Get-Command corepack.cmd -ErrorAction SilentlyContinue
if (-not $corepackCommand) {
    $corepackCommand = Get-Command corepack -ErrorAction SilentlyContinue
}
if (-not $corepackCommand) {
    throw 'Node 22 and Corepack are required to start the doctor website.'
}
$corepack = $corepackCommand.Source
if (-not (Test-Path $clientRoot)) {
    throw 'Nexa Care client workspace missing.'
}

# This is an explicit synthetic-only read-only preflight. No migrations or seeding.
& $python (Join-Path $repoRoot 'scripts\demo_preflight.py') --discovery public-id
if ($LASTEXITCODE -ne 0) {
    Write-Host 'NEXA WEB STACK: NO-GO (environment prerequisites)' -ForegroundColor Red
    throw 'Fix the isolated development database/Redis/schema/provider setup before startup.'
}

Assert-FreeLocalPort -Port 8000
Assert-FreeLocalPort -Port 3000

# Local-only backend. Never expose this development auth surface on a public interface.
$backendBody = @"
Remove-Item Env:ENV -ErrorAction SilentlyContinue
`$env:ENVIRONMENT = 'development'
& '$python' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info
"@
Start-LocalWindow -Title 'Nexa Care - Local Backend' -WorkingDirectory $repoRoot -Body $backendBody

# Browser cookie + CSRF auth must stay on the doctor origin. Next proxies /api.
$doctorBody = @"
`$env:NEXT_PUBLIC_API_URL = 'http://localhost:3000'
`$env:API_PROXY_TARGET = 'http://127.0.0.1:8000'
& '$corepack' yarn workspace next-app dev --hostname localhost --port 3000
"@
Start-LocalWindow -Title 'Nexa Care - Local Doctor Web' -WorkingDirectory $clientRoot -Body $doctorBody

# Separate readiness checks: port listeners or launched windows never mean GO.
$ready = $false
for ($attempt = 1; $attempt -le 12; $attempt++) {
    Start-Sleep -Seconds 2
    & $python (Join-Path $repoRoot 'scripts\check_nexa_web_stack.py')
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
}
if (-not $ready) {
    Write-Host 'NEXA WEB STACK: NO-GO (backend/doctor not ready)' -ForegroundColor Red
    throw 'Inspect the two local development windows; no production state was changed.'
}

Write-Host 'NEXA WEB STACK: LOCAL PREREQUISITES READY' -ForegroundColor Green
Write-Warning 'No phone, signed consent, NFC, browser-to-credential E2E or clinical release was verified.'
Write-Host '  Backend: http://127.0.0.1:8000/health'
Write-Host '  Doctor:  http://localhost:3000/doctor/login'
if (-not $NoBrowser) {
    Start-Process 'http://localhost:3000/doctor/login'
}
