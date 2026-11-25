param(
    [string]$ResourceGroup = 'rg-forescout-kql-demo',
    [string]$Location = '',
    [switch]$UseExistingFoundation
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Invoke-AzJson {
    param([string[]]$Arguments)
    $result = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure command failed: $($Arguments[0..1] -join ' ')" }
    if ($result) { return ($result -join "`n" | ConvertFrom-Json) }
}

$configuration = & '.\.venv\Scripts\python.exe' -c @'
import json
from security_kql.config import Settings
from security_kql.evaluation import reference_info
from security_kql.state import StateStore
settings = Settings.from_env()
store = StateStore(settings.state_dir)
if not reference_info(store)["approved"]:
    raise SystemExit("Explicitly review and approve the local demo reference before public deployment.")
print(json.dumps({
    "project_resource_id": settings.project_resource_id,
    "model": settings.model_name,
    "agent": settings.agent_name,
    "approval": store.get("reference_approval"),
}))
'@
if ($LASTEXITCODE -ne 0) { throw 'Could not load the reviewed demo configuration.' }
$configuration = $configuration | ConvertFrom-Json
$subscription = $configuration.project_resource_id.Split('/')[2]
$projectGroup = $configuration.project_resource_id.Split('/')[4]
if ($projectGroup -ne $ResourceGroup) { throw 'The target group differs from the configured project.' }
$group = Invoke-AzJson @('group', 'show', '--name', $ResourceGroup, '--subscription', $subscription)
if ($group.tags.purpose -ne 'forescout-kql-demo') { throw 'The resource group is not tagged for this demo.' }
if (-not $Location) { $Location = $group.location }
New-Item -ItemType Directory -Force -Path '.azure' | Out-Null

if ($UseExistingFoundation) {
    $foundation = Invoke-AzJson @(
        'deployment', 'group', 'show', '--subscription', $subscription,
        '--resource-group', $ResourceGroup, '--name', 'security-kql-web-foundation'
    )
    if ($foundation.properties.provisioningState -ne 'Succeeded') {
        throw 'Only a successful hosting foundation can be reused.'
    }
}
else {
    $foundation = Invoke-AzJson @(
        'deployment', 'group', 'create', '--subscription', $subscription,
        '--resource-group', $ResourceGroup, '--name', 'security-kql-web-foundation',
        '--template-file', 'infra\web.bicep', '--parameters', "location=$Location"
    )
}
$foundation.properties.outputs | ConvertTo-Json -Depth 10 |
    Set-Content -LiteralPath '.azure\web-foundation.json' -Encoding utf8

$tag = 'web-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
$contextPath = Join-Path $root ('.azure\container-source-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $contextPath | Out-Null
try {
    $files = @(Get-Item -LiteralPath 'Dockerfile', 'pyproject.toml', 'requirements.txt')
    $files += Get-ChildItem -LiteralPath 'src\security_kql' -Recurse -File | Where-Object {
        $_.Extension -in @('.py', '.html', '.json', '.kql') -and $_.DirectoryName -notmatch '__pycache__'
    }
    foreach ($file in $files) {
        $relative = [System.IO.Path]::GetRelativePath($root, $file.FullName)
        $destination = Join-Path $contextPath $relative
        New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }
    & az acr build --subscription $subscription `
        --registry $foundation.properties.outputs.registryName.value `
        --image "security-kql-demo:$tag" --file Dockerfile --no-logs `
        --only-show-errors --output none $contextPath
    if ($LASTEXITCODE -ne 0) { throw 'The Azure-hosted container build failed.' }
}
finally {
    Remove-Item -LiteralPath $contextPath -Recurse
}

$parametersPath = Join-Path $root '.azure\web.parameters.json'
@{
    '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters = @{
        location = @{ value = $Location }
        imageTag = @{ value = $tag }
        modelName = @{ value = $configuration.model }
        agentName = @{ value = $configuration.agent }
        referenceApproval = @{ value = ($configuration.approval | ConvertTo-Json -Compress -Depth 10) }
    }
} | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $parametersPath -Encoding utf8
try {
    $deployment = Invoke-AzJson @(
        'deployment', 'group', 'create', '--subscription', $subscription,
        '--resource-group', $ResourceGroup, '--name', 'security-kql-web',
        '--template-file', 'infra\web-app.bicep', '--parameters', "@$parametersPath"
    )
}
finally {
    Remove-Item -LiteralPath $parametersPath -ErrorAction SilentlyContinue
}
$deployment.properties.outputs | ConvertTo-Json -Depth 10 |
    Set-Content -LiteralPath '.azure\web-deployment.json' -Encoding utf8
$url = $deployment.properties.outputs.appUrl.value
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$ready = $false
$lastError = 'No healthy response.'
while ($timer.Elapsed.TotalMinutes -lt 5) {
    try {
        $health = Invoke-RestMethod "$url/health" -TimeoutSec 15
        $publicConfig = Invoke-RestMethod "$url/api/config" -TimeoutSec 15
        if ($health.status -ne 'ok' -or $health.mode -ne 'azure' -or
            -not $publicConfig.public_demo -or -not $publicConfig.reference.approved) {
            throw 'The public Azure demo is not ready with its reviewed reference.'
        }
        $ready = $true
        break
    }
    catch {
        $lastError = $_.Exception.Message
        Start-Sleep -Seconds 10
    }
}
if (-not $ready) {
    throw "Azure resources were provisioned, but public readiness failed: $lastError"
}
Write-Host "Public demo: $url"
