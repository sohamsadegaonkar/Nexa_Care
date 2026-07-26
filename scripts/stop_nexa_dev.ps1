Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

foreach ($port in 8000, 3000, 8081) {
    $processIds = @(
        Get-NetTCPConnection `
            -LocalPort $port `
            -State Listen `
            -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    )

    foreach ($processId in $processIds) {
        Write-Host "Stopping PID $processId on port $port..."
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

$adb = 'C:\Android\Sdk\platform-tools\adb.exe'

if (Test-Path $adb) {
    & $adb reverse --remove-all 2>$null
}

Write-Host 'Nexa Care development services stopped.'
