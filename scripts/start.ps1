param([int]$Port = 8017)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& '.\.venv\Scripts\python.exe' -m uvicorn security_kql.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
