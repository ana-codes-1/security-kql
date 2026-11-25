$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$outputs = Get-Content -Raw -LiteralPath '.azure\deployment.json' | ConvertFrom-Json

try {
    & '.\.venv\Scripts\python.exe' -m security_kql.cli seed
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic data seeding failed.' }
}
finally {
    & az resource delete --ids $outputs.seedAdminResourceId.value --api-version 2024-04-13 --only-show-errors --output none
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not remove temporary seeding permission. Do not start the app until it is removed.'
    }
}

& '.\.venv\Scripts\python.exe' -m security_kql.cli verify
if ($LASTEXITCODE -ne 0) { throw 'Live ADX fixture verification failed.' }
& '.\.venv\Scripts\python.exe' -m security_kql.cli register
if ($LASTEXITCODE -ne 0) { throw 'Foundry registration failed.' }
Write-Host 'Fixtures verified; temporary admin removed; Foundry agent and evaluator registered.'
