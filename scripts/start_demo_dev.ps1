<#
.SYNOPSIS
Creates and runs an isolated Nexa Care development demo stack.

.DESCRIPTION
The default invocation only starts processes.  Database/Redis creation,
migrations, and synthetic-data seeding each require an explicit switch.

The script creates only an ignored `.env.demo.local` file and loopback Docker
containers named for this disposable demo.  It never overwrites `.env`, stops
an existing process, or targets a non-loopback/non-disposable database.
#>

[CmdletBinding()]
param(
    [switch]$InitializeInfrastructure,
    [switch]$Migrate,
    [switch]$Seed,
    [switch]$StartExpo,
    [switch]$SkipBackend,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1024, 65535)][int]$WebPort = 3000,
    [ValidateRange(1024, 65535)][int]$MetroPort = 8081,
    [string]$MobileApiUrl,
    [string]$MobileClientCidr
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot '.env.demo.local'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$clientRoot = Join-Path $repoRoot 'nexa-client'
$postgresContainer = 'nexa_demo_postgres_20260922'
$redisContainer = 'nexa_demo_redis_20260922'
$databaseName = 'nexa_qual_demo_20260922'
$databaseUser = 'nexa_demo'
$postgresPort = 55432
$redisPort = 56379

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command was not found: $Name"
    }
}

function Test-ListeningPort {
    param([Parameter(Mandatory)][int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Assert-FreePort {
    param([Parameter(Mandatory)][int]$Port, [Parameter(Mandatory)][string]$Purpose)
    if (Test-ListeningPort -Port $Port) {
        throw "$Purpose port $Port is already in use. This script will not stop an unknown process."
    }
}

function Test-PrivateIpv4Address {
    param([Parameter(Mandatory)][string]$Value)
    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($Value, [ref]$address)) { return $false }
    if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    $octets = $address.GetAddressBytes()
    return (
        $octets[0] -eq 10 -or
        ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31) -or
        ($octets[0] -eq 192 -and $octets[1] -eq 168)
    )
}

function Test-PrivateIpv4Cidr {
    param([Parameter(Mandatory)][string]$Value)
    $match = [regex]::Match($Value.Trim(), '^(?<address>[^/]+)/(?<prefix>\d{1,2})$')
    if (-not $match.Success) { return $false }

    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($match.Groups['address'].Value, [ref]$address)) { return $false }
    if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $false }

    $prefixLength = [int]$match.Groups['prefix'].Value
    if ($prefixLength -gt 32) { return $false }

    # The CIDR must be fully contained in an RFC1918 allocation.  A private
    # starting address with a broad prefix (for example 192.168.1.0/8) would
    # otherwise authorize public source addresses after normalization.
    $octets = $address.GetAddressBytes()
    return (
        ($octets[0] -eq 10 -and $prefixLength -ge 8) -or
        ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31 -and $prefixLength -ge 12) -or
        ($octets[0] -eq 192 -and $octets[1] -eq 168 -and $prefixLength -ge 16)
    )
}

function New-UrlSafeSecret {
    param([ValidateRange(32, 256)][int]$Bytes = 48)
    $buffer = [byte[]]::new($Bytes)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($buffer)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_')
}

function New-TotpSecret {
    # RFC 4648 base32 alphabet; 32 is a power of two, so every random byte
    # maps uniformly with the low five bits. The value is stored only in the
    # ignored local demo environment and is never displayed.
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.ToCharArray()
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return -join ($bytes | ForEach-Object { $alphabet[$_ -band 31] })
}

function Import-DemoEnvironment {
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw "Missing ignored demo configuration: $envFile. Run with -InitializeInfrastructure first."
    }

    Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            Set-Item -Path "Env:$name" -Value $value
        }
    }
    $env:NEXA_DEMO_ENV_FILE = $envFile
}

function Assert-DisposableDemoTarget {
    if ($env:ENVIRONMENT -ne 'development') {
        throw 'Demo startup requires ENVIRONMENT=development.'
    }
    if (-not $env:DATABASE_URL) {
        throw 'DATABASE_URL is missing from the demo environment.'
    }
    try {
        $databaseUri = [Uri]$env:DATABASE_URL
    }
    catch {
        throw 'DATABASE_URL is not a valid URI.'
    }
    if ($databaseUri.Host -notin @('127.0.0.1', 'localhost', '::1')) {
        throw 'Refusing a non-loopback database target.'
    }
    $targetDatabase = $databaseUri.AbsolutePath.Trim('/')
    if (-not $targetDatabase.StartsWith('nexa_qual_demo_', [System.StringComparison]::Ordinal)) {
        throw 'Refusing a database target that is not explicitly disposable.'
    }
    if (-not $env:UPSTASH_REDIS_URL) {
        throw 'UPSTASH_REDIS_URL is missing from the demo environment.'
    }
    try {
        $redisUri = [Uri]$env:UPSTASH_REDIS_URL
    }
    catch {
        throw 'UPSTASH_REDIS_URL is not a valid URI.'
    }
    if ($redisUri.Host -notin @('127.0.0.1', 'localhost', '::1')) {
        throw 'Refusing a non-loopback Redis target.'
    }
}

function Wait-ContainerHealthy {
    param([Parameter(Mandatory)][string]$ContainerName)
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $health = (& docker inspect --format '{{.State.Health.Status}}' $ContainerName 2>$null).Trim()
        if ($LASTEXITCODE -eq 0 -and $health -eq 'healthy') {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Container did not become healthy: $ContainerName"
}

function Test-DockerContainerExists {
    param([Parameter(Mandatory)][string]$ContainerName)
    # Docker's normal "not found" response is an expected false result here;
    # do not let PowerShell's native-command error preference turn it into a
    # terminating setup failure.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & docker container inspect $ContainerName 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Initialize-DisposableInfrastructure {
    if (Test-Path -LiteralPath $envFile) {
        throw "Refusing to overwrite existing ignored demo configuration: $envFile"
    }
    Assert-Command -Name docker
    & docker version --format '{{.Server.Version}}' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Desktop is not ready. Start it, wait for it to finish initializing, then retry.'
    }
    Assert-FreePort -Port $postgresPort -Purpose 'Disposable PostgreSQL'
    Assert-FreePort -Port $redisPort -Purpose 'Disposable Redis'
    foreach ($container in @($postgresContainer, $redisContainer)) {
        if (Test-DockerContainerExists -ContainerName $container) {
            throw "Refusing to reuse existing demo container: $container"
        }
    }

    $postgresPassword = New-UrlSafeSecret
    $fernetMfa = New-UrlSafeSecret -Bytes 32
    $fernetPii = New-UrlSafeSecret -Bytes 32
    $storageKey = New-UrlSafeSecret -Bytes 32
    $discoveryKey = New-UrlSafeSecret
    $lines = @(
        '# Generated for a local disposable development demo. Never commit this file.',
        'ENVIRONMENT=development',
        'NEXA_DEMO_ALLOW_INSECURE_LOOPBACK_WEB_COOKIES=true',
        'NEXA_DEMO_PATIENT_LOGIN_ENABLED=true',
        "DATABASE_URL=postgresql+asyncpg://${databaseUser}:${postgresPassword}@127.0.0.1:${postgresPort}/${databaseName}",
        "UPSTASH_REDIS_URL=redis://127.0.0.1:${redisPort}/0",
        'SUPABASE_URL=http://127.0.0.1:54321',
        'SUPABASE_KEY=local-demo-supabase-placeholder',
        "HANDSHAKE_PEPPER_SECRET=$(New-UrlSafeSecret)",
        "KEK_ROOT_SECRET=$(New-UrlSafeSecret)",
        "MFA_ENCRYPTION_KEY=$fernetMfa",
        "PII_ENCRYPTION_KEY=$fernetPii",
        "PATIENT_JWT_SECRET=$(New-UrlSafeSecret)",
        "OTP_RATE_LIMIT_HMAC_SECRET=$(New-UrlSafeSecret)",
        "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET=$(New-UrlSafeSecret)",
        "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET=$(New-UrlSafeSecret)",
        "PATIENT_GRANT_REFERENCE_HMAC_SECRET=$(New-UrlSafeSecret)",
        ("PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON='" + (@{ '1' = $discoveryKey } | ConvertTo-Json -Compress) + "'"),
        'PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION=1',
        "CLINIC_API_KEY=$(New-UrlSafeSecret)",
        "DEMO_PROVIDER_PASSWORD=Nexa!9a$(New-UrlSafeSecret -Bytes 32)",
        "DEMO_PROVIDER_TOTP_SECRET=$(New-TotpSecret)",
        'DOCUMENT_EXTRACTION_PROVIDER=demo',
        'DOCUMENT_STORAGE_PROVIDER=local',
        'DOCUMENT_STORAGE_LOCAL_ROOT=.local/demo-document-storage',
        "DOCUMENT_STORAGE_ENCRYPTION_KEY=$storageKey",
        'ENCRYPTION_BACKEND=local',
        'DOCUMENT_AI_ASYNC_MULTIPAGE_ENABLED=false',
        'CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000',
        'TRUSTED_HOSTS=localhost,127.0.0.1,10.0.2.2',
        'PATIENT_TERMS_VERSION=demo-2026-09-22',
        ('PATIENT_TERMS_SHA256=' + ('a' * 64)),
        'PATIENT_TERMS_URL=https://legal.nexa.test/demo/terms',
        'PATIENT_PRIVACY_VERSION=demo-2026-09-22',
        ('PATIENT_PRIVACY_SHA256=' + ('b' * 64)),
        'PATIENT_PRIVACY_URL=https://legal.nexa.test/demo/privacy'
    )

    & docker run --detach --name $postgresContainer `
        --label 'com.nexacare.demo=true' --label 'com.nexacare.disposable=true' `
        --health-cmd "pg_isready -U $databaseUser -d $databaseName" `
        --health-interval 5s --health-timeout 3s --health-retries 12 `
        --env "POSTGRES_USER=$databaseUser" --env "POSTGRES_PASSWORD=$postgresPassword" `
        --env "POSTGRES_DB=$databaseName" --publish "127.0.0.1:${postgresPort}:5432" `
        postgres:16-alpine | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the disposable PostgreSQL container.' }

    & docker run --detach --name $redisContainer `
        --label 'com.nexacare.demo=true' --label 'com.nexacare.disposable=true' `
        --health-cmd 'redis-cli ping' --health-interval 5s --health-timeout 3s --health-retries 12 `
        --publish "127.0.0.1:${redisPort}:6379" redis:7-alpine redis-server --appendonly no --save '' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the disposable Redis container.' }

    Wait-ContainerHealthy -ContainerName $postgresContainer
    Wait-ContainerHealthy -ContainerName $redisContainer
    [System.IO.File]::WriteAllLines($envFile, $lines, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Created isolated loopback containers and ignored demo configuration: $envFile"
    Write-Host 'Next explicit step: .\scripts\start_demo_dev.ps1 -Migrate -Seed'
}

function Ensure-DemoProviderTotpSecret {
    Import-DemoEnvironment
    if (-not [string]::IsNullOrWhiteSpace($env:DEMO_PROVIDER_TOTP_SECRET)) {
        return
    }
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw "Demo configuration is missing: $envFile"
    }
    $secret = New-TotpSecret
    [System.IO.File]::AppendAllText(
        $envFile,
        [Environment]::NewLine + "DEMO_PROVIDER_TOTP_SECRET=$secret" + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    $env:DEMO_PROVIDER_TOTP_SECRET = $secret
}

function Ensure-DemoPatientLoginConfiguration {
    Import-DemoEnvironment
    if (-not [string]::IsNullOrWhiteSpace($env:NEXA_DEMO_PATIENT_LOGIN_ENABLED)) {
        return
    }
    # This only upgrades an older ignored generated demo config.  An explicit
    # false setting remains operator-controlled and is never overwritten.
    [System.IO.File]::AppendAllText(
        $envFile,
        [Environment]::NewLine + 'NEXA_DEMO_PATIENT_LOGIN_ENABLED=true' + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    $env:NEXA_DEMO_PATIENT_LOGIN_ENABLED = 'true'
}

function Invoke-DemoMigration {
    Import-DemoEnvironment
    Assert-DisposableDemoTarget
    if (-not (Test-Path -LiteralPath $python)) { throw "Python 3.12 venv not found: $python" }
    # Alembic prioritizes TEST_DATABASE_URL; pin it to the verified disposable target.
    $env:TEST_DATABASE_URL = $env:DATABASE_URL
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed.' }
    $revision = (& $python -m alembic current).Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -notmatch '20260919_medication_catalog') {
        throw 'Disposable database did not reach 20260919_medication_catalog.'
    }
    Write-Host "Migration complete: $revision"
}

function Invoke-DemoSeed {
    Import-DemoEnvironment
    Assert-DisposableDemoTarget
    Ensure-DemoProviderTotpSecret
    Ensure-DemoPatientLoginConfiguration
    if (-not (Test-Path -LiteralPath $python)) { throw "Python 3.12 venv not found: $python" }
    & $python scripts/seed_demo_doctor.py
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic demo seeding failed.' }
}

function Start-DemoProcess {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [hashtable]$Environment = @{}
    )
    $logDirectory = Join-Path $env:TEMP 'NexaCareDemo'
    [System.IO.Directory]::CreateDirectory($logDirectory) | Out-Null
    $safeName = $Name -replace '[^A-Za-z0-9_-]', '-'
    $stdout = Join-Path $logDirectory "$safeName.stdout.log"
    $stderr = Join-Path $logDirectory "$safeName.stderr.log"

    # Windows PowerShell 5.1 cannot provide a per-process environment map to
    # Start-Process.  Temporarily apply only the explicitly supplied values so
    # the child inherits them, then restore the launcher environment.
    $previous = @{}
    foreach ($entry in $Environment.GetEnumerator()) {
        $previous[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
        [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value, 'Process')
    }
    try {
        $process = Start-Process -FilePath $FilePath -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru -ArgumentList $ArgumentList -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    }
    finally {
        foreach ($entry in $previous.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
        }
    }
    Write-Host "Started $Name (PID $($process.Id)); logs: $logDirectory"
}

function Start-DemoStack {
    Import-DemoEnvironment
    Assert-DisposableDemoTarget
    if (-not (Test-Path -LiteralPath $python)) { throw "Python 3.12 venv not found: $python" }
    Assert-Command -Name corepack
    if (-not $SkipBackend) { Assert-FreePort -Port $BackendPort -Purpose 'Backend' }
    Assert-FreePort -Port $WebPort -Purpose 'Doctor web'
    if ($StartExpo) { Assert-FreePort -Port $MetroPort -Purpose 'Expo Metro' }

    $resolvedMobileApiUrl = $MobileApiUrl
    if (-not $resolvedMobileApiUrl) {
        $resolvedMobileApiUrl = "http://10.0.2.2:$BackendPort"
    }
    try {
        $mobileUri = [Uri]$resolvedMobileApiUrl
        $mobileHost = $mobileUri.Host
    }
    catch {
        throw 'MobileApiUrl must be a valid http(s) URL.'
    }
    if ($mobileUri.Scheme -ne 'http') {
        throw 'The disposable mobile demo accepts an explicit http:// Android API URL only.'
    }
    $usesBuiltInMobileTransport = $mobileHost -in @('10.0.2.2', 'localhost', '127.0.0.1') -or $mobileHost.StartsWith('127.')
    $demoPatientAllowedHosts = ''
    $demoPatientAllowedClientCidrs = ''
    if (-not $usesBuiltInMobileTransport) {
        if (-not (Test-PrivateIpv4Address -Value $mobileHost)) {
            throw 'A physical-device MobileApiUrl must use a literal RFC1918 IPv4 host.'
        }
        if ([string]::IsNullOrWhiteSpace($MobileClientCidr)) {
            throw 'A physical-device MobileApiUrl requires -MobileClientCidr (for example 192.168.1.0/24).'
        }
        if (-not (Test-PrivateIpv4Cidr -Value $MobileClientCidr)) {
            throw 'MobileClientCidr must be an RFC1918 IPv4 CIDR contained within 10/8, 172.16/12, or 192.168/16.'
        }
        $demoPatientAllowedHosts = $mobileHost
        $demoPatientAllowedClientCidrs = $MobileClientCidr.Trim()
    }
    elseif (-not [string]::IsNullOrWhiteSpace($MobileClientCidr)) {
        throw '-MobileClientCidr is only valid with a physical-device RFC1918 MobileApiUrl.'
    }
    # The ordinary emulator/browser path is loopback-only.  A physical device
    # is an explicit exception: bind only to the caller's literal private
    # address, while the API independently enforces the supplied client CIDR.
    $backendBindHost = if ($usesBuiltInMobileTransport) { '127.0.0.1' } else { $mobileHost }
    $trustedHosts = @($env:TRUSTED_HOSTS -split ',' | Where-Object { $_ })
    if ($trustedHosts -notcontains $mobileHost) { $trustedHosts += $mobileHost }
    $trustedHostsValue = $trustedHosts -join ','
    $corsOrigins = "http://localhost:$WebPort,http://127.0.0.1:$WebPort"

    if (-not $SkipBackend) {
        Start-DemoProcess -Name 'FastAPI backend' -FilePath $python -WorkingDirectory $repoRoot -ArgumentList @(
            '-m', 'uvicorn', 'app.main:app', '--host', $backendBindHost, '--port', [string]$BackendPort, '--log-level', 'info'
        ) -Environment @{
            CORS_ALLOWED_ORIGINS = $corsOrigins
            TRUSTED_HOSTS = $trustedHostsValue
            NEXA_DEMO_PATIENT_LOGIN_ALLOWED_HOSTS = $demoPatientAllowedHosts
            NEXA_DEMO_PATIENT_LOGIN_ALLOWED_CLIENT_CIDRS = $demoPatientAllowedClientCidrs
        }
    }

    $node = (Get-Command node -ErrorAction Stop).Source
    $nextCli = Join-Path $clientRoot 'node_modules\next\dist\bin\next'
    $nextAppRoot = Join-Path $clientRoot 'apps\next'
    if (-not (Test-Path -LiteralPath $nextCli)) { throw "Next.js CLI not found: $nextCli" }
    if (-not (Test-Path -LiteralPath $nextAppRoot)) { throw "Next.js app root not found: $nextAppRoot" }
    Start-DemoProcess -Name 'Doctor Next.js web' -FilePath $node -WorkingDirectory $nextAppRoot -ArgumentList @(
        $nextCli, 'dev', '--hostname', '127.0.0.1', '--port', [string]$WebPort
    ) -Environment @{
        # The browser stays on the Next origin; its `/api` traffic is proxied
        # server-side to the loopback backend so session cookies remain
        # first-party even in the disposable HTTP-only development demo.
        NEXT_PUBLIC_API_URL = "http://127.0.0.1:$WebPort"
        API_PROXY_TARGET = "http://${backendBindHost}:$BackendPort"
        NEXA_NEXT_DIST_DIR = ".next-demo-$WebPort"
    }

    if ($StartExpo) {
        $expoDemoPatientLogin = if ($env:NEXA_DEMO_PATIENT_LOGIN_ENABLED -eq 'true') { 'true' } else { 'false' }
        # Expo defaults to LAN binding when no host mode is supplied.  Keep the
        # ordinary emulator/browser demo loopback-only; a physical-device
        # session is the explicit exception already validated above.
        $expoHostMode = if ($usesBuiltInMobileTransport) { 'localhost' } else { 'lan' }
        $expoAdvertisedHost = if ($usesBuiltInMobileTransport) { '127.0.0.1' } else { $mobileHost }
        $metroBindShim = Join-Path $repoRoot 'scripts\metro_bind_host.cjs'
        if (-not (Test-Path -LiteralPath $metroBindShim)) { throw "Metro bind shim not found: $metroBindShim" }
        # NODE_OPTIONS on Windows treats backslashes in an unquoted absolute
        # path as escapes.  Forward slashes keep the preload path intact.
        $metroBindShimForNode = $metroBindShim.Replace('\', '/')
        $metroNodeOptions = "--require=$metroBindShimForNode"
        if (-not [string]::IsNullOrWhiteSpace($env:NODE_OPTIONS)) {
            $metroNodeOptions = "$env:NODE_OPTIONS $metroNodeOptions"
        }
        Start-DemoProcess -Name 'Patient Expo Metro' -FilePath 'cmd.exe' -WorkingDirectory $clientRoot -ArgumentList @(
            '/d', '/s', '/c', "corepack yarn workspace expo-app start --host $expoHostMode --port $MetroPort"
        ) -Environment @{
            EXPO_PUBLIC_API_URL = $resolvedMobileApiUrl
            EXPO_PUBLIC_APP_ENV = 'development'
            EXPO_PUBLIC_ALLOW_HTTP = 'true'
            EXPO_PUBLIC_NEXA_DEMO_PATIENT_LOGIN = $expoDemoPatientLogin
            NEXA_METRO_BIND_HOST = $expoAdvertisedHost
            NEXA_METRO_BIND_PORT = [string]$MetroPort
            NODE_OPTIONS = $metroNodeOptions
        }
    }

    Write-Host ''
    Write-Host 'Nexa Care disposable development demo started:' -ForegroundColor Green
    Write-Host "  Backend:    http://${backendBindHost}:$BackendPort"
    Write-Host "  Doctor:     http://127.0.0.1:$WebPort/doctor/login"
    if ($StartExpo) {
        Write-Host "  Metro:      http://${expoAdvertisedHost}:$MetroPort ($expoHostMode)"
        Write-Host "  Android API: $resolvedMobileApiUrl"
        Write-Host '  Android development build: from nexa-client run corepack yarn workspace expo-app android'
        if ($demoPatientAllowedClientCidrs) {
            Write-Host "  Demo Android clients: $demoPatientAllowedClientCidrs"
        }
    }
    Write-Host 'The ignored DEMO_PROVIDER_PASSWORD remains in .env.demo.local and is never printed.'
}

if ($InitializeInfrastructure) { Initialize-DisposableInfrastructure }
if ($Migrate) { Invoke-DemoMigration }
if ($Seed) { Invoke-DemoSeed }
if (-not ($InitializeInfrastructure -or $Migrate -or $Seed)) { Start-DemoStack }
