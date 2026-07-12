<#
.SYNOPSIS
Safely prepares and validates local Phase 2 alpha environment files.
.DESCRIPTION
Creates .env.alpha from the tracked template when absent. Validation never
prints values. -CopyToDotEnv validates first, backs up an existing .env, and
then copies .env.alpha to the runtime .env file.
#>


[CmdletBinding()]
param(
    [switch]$CopyToDotEnv,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$required = @(
    'SUPABASE_URL', 'SUPABASE_KEY', 'DATABASE_URL', 'UPSTASH_REDIS_URL',
    'HANDSHAKE_PEPPER_SECRET', 'KEK_ROOT_SECRET', 'MFA_ENCRYPTION_KEY',
    'PII_ENCRYPTION_KEY', 'CLINIC_API_KEY'
)
$placeholderPattern = '(?i)(your-project|your-service-role-key|username:password|user:pass|change-me|REPLACE_WITH|GENERATED_|<[^>]+>)'

if (-not (Test-Path '.git') -or -not (Test-Path '.env.example') -or -not (Test-Path 'app/core/config.py')) {
    Write-Error 'Run this script from the Nexa Care repository root.'
}

if ($ValidateOnly -and $CopyToDotEnv) {
    Write-Error '-ValidateOnly and -CopyToDotEnv cannot be used together.'
}

$branch = (git branch --show-current).Trim()
if ($branch -ne 'alpha-loop-testing') { Write-Error "Expected branch alpha-loop-testing; current branch is $branch." }
if (git status --short) { Write-Warning 'The working tree is dirty. Existing changes will not be modified except requested environment files.' }

foreach ($file in @('.env', '.env.alpha')) {
    git check-ignore -q -- $file
    if ($LASTEXITCODE -ne 0) { Write-Error "$file is not ignored by Git." }
}

if (-not $ValidateOnly -and -not (Test-Path '.env.alpha')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env.alpha'
    Write-Host 'Created .env.alpha from .env.example. Fill it with real alpha credentials.'
}

$source = if (Test-Path '.env.alpha') { '.env.alpha' } elseif (Test-Path '.env') { '.env' } else { $null }
if (-not $source) { Write-Error 'No .env.alpha or .env exists. Run without -ValidateOnly to create .env.alpha.' }

$values = @{}
foreach ($line in Get-Content -LiteralPath $source -Encoding UTF8) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
        $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
    }
}
$rows = foreach ($name in $required) {
    $value = $values[$name]
    [pscustomobject]@{ Variable = $name; Configured = [bool]($value -and $value -notmatch $placeholderPattern) }
}
$rows | Format-Table -AutoSize
$missing = @($rows | Where-Object { -not $_.Configured })
if ($missing.Count) { Write-Error 'Required alpha configuration is missing or contains placeholders. No secret values were displayed.' }

if ($CopyToDotEnv) {
    if (-not (Test-Path '.env.alpha')) { Write-Error '.env.alpha does not exist.' }
    if (Test-Path '.env') {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backup = ".env.$stamp.backup"
        Copy-Item -LiteralPath '.env' -Destination $backup
        Write-Host "Backed up the existing .env to $backup."
    }
    Copy-Item -LiteralPath '.env.alpha' -Destination '.env' -Force
    Write-Host 'Copied validated .env.alpha to .env.'
}

Write-Host 'Configuration names are present. Next: python scripts/check_alpha_environment.py --config-only'
Write-Host 'Then: python scripts/check_alpha_environment.py --all'
