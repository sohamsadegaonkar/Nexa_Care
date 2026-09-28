[CmdletBinding()]
param(
    [switch]$Restart,
    [switch]$NoBrowser,
    [switch]$OpenAndroid
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$clientRoot = Join-Path $repoRoot 'nexa-client'
$expoRoot = Join-Path $clientRoot 'apps\expo'

function Resolve-RepoPython {
    $candidates = @()
    if ($env:VIRTUAL_ENV) {
        $candidates += (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe')
    }
    $candidates += (Join-Path $repoRoot '.venv\Scripts\python.exe')
    $candidates += (Join-Path $repoRoot 'venv\Scripts\python.exe')

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    throw 'No repository Python environment found. Activate the repo venv or create .venv/venv.'
}

$python = Resolve-RepoPython

$javaCandidates = @(
    $env:JAVA_HOME,
    'C:\Program Files\Microsoft\jdk-17',
    'C:\Program Files\Eclipse Adoptium\jdk-17'
) | Where-Object { $_ }
$javaHome = $javaCandidates |
    Where-Object { Test-Path (Join-Path $_ 'bin\java.exe') } |
    Select-Object -First 1
if (-not $javaHome) {
    throw 'Java 17 was not found. Set JAVA_HOME to a JDK 17 installation.'
}

$androidCandidates = @(
    $env:ANDROID_SDK_ROOT,
    $env:ANDROID_HOME,
    'C:\Android\Sdk'
) | Where-Object { $_ }
$androidSdk = $androidCandidates |
    Where-Object { Test-Path (Join-Path $_ 'platform-tools\adb.exe') } |
    Select-Object -First 1
if (-not $androidSdk) {
    throw 'Android SDK/adb was not found. Set ANDROID_SDK_ROOT or ANDROID_HOME.'
}
$androidNdk = Join-Path $androidSdk 'ndk\27.1.12297006'
$adb = Join-Path $androidSdk 'platform-tools\adb.exe'

$corepackCommand = Get-Command corepack.cmd -ErrorAction SilentlyContinue
if (-not $corepackCommand) {
    $corepackCommand = Get-Command corepack -ErrorAction SilentlyContinue
}

if (-not $corepackCommand) {
    throw 'Corepack was not found. Confirm Node.js 22 is installed.'
}

$corepack = $corepackCommand.Source

$requiredPaths = [ordered]@{
    'Repository'  = $repoRoot
    'Python venv' = $python
    'Java 17'     = $javaHome
    'Android SDK' = $androidSdk
    'ADB'         = $adb
    'Client'      = $clientRoot
    'Expo app'    = $expoRoot
}

foreach ($item in $requiredPaths.GetEnumerator()) {
    if (-not (Test-Path $item.Value)) {
        throw "$($item.Key) was not found: $($item.Value)"
    }
}

& $python -c "import sys, alembic, fastapi, sqlalchemy; print(f'python={sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw 'Repository Python or required backend dependencies are unavailable.'
}
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$javaVersion = (& (Join-Path $javaHome 'bin\java.exe') -version 2>&1 | Out-String)
$ErrorActionPreference = $prevErrorAction
if ($javaVersion -notmatch 'version "17\.') {
    throw 'Nexa Care Android development requires Java 17.'
}

$localGoogleServices = Join-Path $expoRoot 'google-services.development.local.json'
$googleServicesValue = $null
if (Test-Path $localGoogleServices) {
    $googleServicesValue = './google-services.development.local.json'
}
elseif ($env:GOOGLE_SERVICES_FILE) {
    $configuredGoogleServices = if ([IO.Path]::IsPathRooted($env:GOOGLE_SERVICES_FILE)) {
        $env:GOOGLE_SERVICES_FILE
    } else {
        Join-Path $expoRoot $env:GOOGLE_SERVICES_FILE
    }
    if (-not (Test-Path $configuredGoogleServices)) {
        throw "Configured GOOGLE_SERVICES_FILE does not exist: $configuredGoogleServices"
    }
    $googleServicesValue = $env:GOOGLE_SERVICES_FILE
}
else {
    Write-Warning 'Firebase Android client config not present; push-dependent demo features may be unavailable.'
}

& $python (Join-Path $repoRoot 'scripts\demo_preflight.py')
if ($LASTEXITCODE -ne 0) {
    Write-Host 'NEXA STARTUP: NO-GO' -ForegroundColor Red
    throw 'Development preflight failed. No automatic database mutation was performed.'
}

function Get-PortProcesses {
    param([Parameter(Mandatory)][int]$Port)

    @(
        Get-NetTCPConnection `
            -LocalPort $Port `
            -State Listen `
            -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Stop-DevelopmentPort {
    param([Parameter(Mandatory)][int]$Port)

    foreach ($processId in @(Get-PortProcesses -Port $Port)) {
        try {
            Write-Host "Stopping PID $processId on port $Port..."
            Stop-Process -Id $processId -Force -ErrorAction Stop
        }
        catch {
            Write-Warning "Could not stop PID $processId on port $Port."
        }
    }
}

function Start-DevelopmentWindow {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$Body
    )

    $childScript = @"
`$Host.UI.RawUI.WindowTitle = '$Title'
`$ErrorActionPreference = 'Stop'

`$env:JAVA_HOME = '$javaHome'
`$env:ANDROID_HOME = '$androidSdk'
`$env:ANDROID_SDK_ROOT = '$androidSdk'
`$env:ANDROID_NDK_HOME = '$androidNdk'
`$env:Path = '$androidSdk\platform-tools;$androidSdk\emulator;$javaHome\bin;' + `$env:Path

Set-Location '$WorkingDirectory'

$Body
"@

    $encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($childScript)
    )

    Start-Process `
        -FilePath 'powershell.exe' `
        -ArgumentList @(
            '-NoLogo',
            '-NoExit',
            '-ExecutionPolicy',
            'Bypass',
            '-EncodedCommand',
            $encoded
        ) |
        Out-Null
}

if ($Restart) {
    8000, 3000, 8081 | ForEach-Object {
        Stop-DevelopmentPort -Port $_
    }

    Start-Sleep -Seconds 1
}

# Backend — Uvicorn
if (@(Get-PortProcesses -Port 8000).Count -eq 0) {
    $backendBody = @"
Remove-Item Env:ENV -ErrorAction SilentlyContinue
`$env:ENVIRONMENT = 'development'

& '$python' -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info --access-log
"@

    Start-DevelopmentWindow `
        -Title 'Nexa Care - Uvicorn Backend' `
        -WorkingDirectory $repoRoot `
        -Body $backendBody

    Write-Host 'Started Uvicorn on port 8000.'
}
else {
    Write-Host 'Uvicorn is already listening on port 8000.'
}

# Doctor web portal — Next.js
if (@(Get-PortProcesses -Port 3000).Count -eq 0) {
    $webBody = @"
`$env:NEXT_PUBLIC_API_URL = 'http://localhost:8000'

& '$corepack' yarn workspace next-app dev --hostname localhost --port 3000
"@

    Start-DevelopmentWindow `
        -Title 'Nexa Care - Doctor Web' `
        -WorkingDirectory $clientRoot `
        -Body $webBody

    Write-Host 'Started Next.js on port 3000.'
}
else {
    Write-Host 'Next.js is already listening on port 3000.'
}

# Patient mobile bundler — Expo Metro
if (@(Get-PortProcesses -Port 8081).Count -eq 0) {
    $firebaseLine = if ($googleServicesValue) {
        "`$env:GOOGLE_SERVICES_FILE = '$googleServicesValue'"
    } else {
        "Remove-Item Env:GOOGLE_SERVICES_FILE -ErrorAction SilentlyContinue"
    }
    $metroBody = @"
`$env:EXPO_PUBLIC_API_URL = 'http://127.0.0.1:8000'
`$env:EXPO_PUBLIC_APP_ENV = 'development'
`$env:EXPO_PUBLIC_ALLOW_HTTP = 'true'
$firebaseLine

& '$corepack' yarn workspace expo-app start
"@

    Start-DevelopmentWindow `
        -Title 'Nexa Care - Expo Metro' `
        -WorkingDirectory $clientRoot `
        -Body $metroBody

    Write-Host 'Started Expo Metro on port 8081.'
}
else {
    Write-Host 'Metro is already listening on port 8081.'
}

# Android device and USB forwarding
try {
    & $adb start-server | Out-Null

    $connectedDevices = @(
        & $adb devices |
        Where-Object { $_ -match '^\S+\s+device$' }
    )

    if ($connectedDevices.Count -gt 0) {
        & $adb reverse tcp:8081 tcp:8081 | Out-Null
        & $adb reverse tcp:8000 tcp:8000 | Out-Null

        Write-Host 'ADB device connected.'
        Write-Host 'Forwarded phone port 8081 to Metro.'
        Write-Host 'Forwarded phone port 8000 to Uvicorn.'

        if ($OpenAndroid) {
            Start-Sleep -Seconds 6

            & $adb shell monkey `
                -p ai.nexacare.patient `
                -c android.intent.category.LAUNCHER `
                1 |
                Out-Null
        }
    }
    else {
        Write-Warning 'No authorized Android device was detected.'
        Write-Warning 'Connect the phone, approve USB debugging, then run: nexa -Restart'
    }
}
catch {
    Write-Warning "ADB setup failed: $($_.Exception.Message)"
}

if (-not $NoBrowser) {
    Start-Sleep -Seconds 3
    Start-Process 'http://localhost:3000/doctor/login'
}

Write-Host ''
Write-Host 'NEXA STARTUP: GO' -ForegroundColor Green
Write-Host '  Backend: http://localhost:8000'
Write-Host '  Doctor:  http://localhost:3000'
Write-Host '  Metro:   http://localhost:8081'
