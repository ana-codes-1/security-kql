param(
    [string]$ResourceGroup = 'rg-forescout-kql-demo',
    [string]$Location = 'eastus2',
    [switch]$UseExistingDeployment
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

$account = Invoke-AzJson @('account', 'show')
$operator = Invoke-AzJson @('ad', 'signed-in-user', 'show')
$exists = & az group exists --name $ResourceGroup --output tsv
if ($LASTEXITCODE -ne 0) { throw 'Could not check the resource group.' }
if ($exists -eq 'true') {
    $group = Invoke-AzJson @('group', 'show', '--name', $ResourceGroup)
    if ($group.tags.purpose -ne 'forescout-kql-demo') {
        throw 'Refusing to modify a resource group not tagged for this demo.'
    }
}
else {
    $null = Invoke-AzJson @(
        'group', 'create', '--name', $ResourceGroup, '--location', $Location,
        '--tags', 'purpose=forescout-kql-demo'
    )
}

foreach ($provider in @('Microsoft.Kusto', 'Microsoft.CognitiveServices', 'Microsoft.Insights', 'Microsoft.OperationalInsights')) {
    & az provider register --namespace $provider --wait --only-show-errors --output none
    if ($LASTEXITCODE -ne 0) { throw "Could not register $provider." }
}

if ($UseExistingDeployment) {
    $deployment = Invoke-AzJson @(
        'deployment', 'group', 'show', '--resource-group', $ResourceGroup,
        '--name', 'security-kql-demo'
    )
    if ($deployment.properties.provisioningState -ne 'Succeeded') {
        throw 'Only a successful deployment can be reused.'
    }
}
else {
    Write-Host 'Provisioning the isolated Foundry, telemetry, and ADX resources...'
    $deployment = Invoke-AzJson @(
        'deployment', 'group', 'create', '--resource-group', $ResourceGroup,
        '--name', 'security-kql-demo', '--template-file', 'infra\main.bicep',
        '--parameters', "operatorPrincipalId=$($operator.id)", "location=$Location"
    )
}

$outputs = $deployment.properties.outputs
New-Item -ItemType Directory -Force -Path '.azure' | Out-Null
$principals = Invoke-AzJson @(
    'rest', '--method', 'get', '--url',
    "https://management.azure.com$($outputs.databaseResourceId.value)/principalAssignments?api-version=2024-04-13"
)
$seedAdmin = @($principals.value | Where-Object {
    $_.properties.role -eq 'Admin' -and (
        $_.properties.principalId -eq $operator.id -or
        $_.properties.principalId -eq $operator.userPrincipalName -or
        $_.properties.principalId -eq $account.user.name
    )
})
if ($seedAdmin.Count -eq 1) {
    $seedAdminId = $seedAdmin[0].id
}
elseif ($seedAdmin.Count -eq 0) {
    $seedAdminId = "$($outputs.databaseResourceId.value)/principalAssignments/temporary-seed-admin"
    @{
        properties = @{
            principalId = $operator.id
            principalType = 'User'
            role = 'Admin'
            tenantId = $account.tenantId
        }
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '.azure\seed-admin.json' -Encoding utf8
    try {
        $null = Invoke-AzJson @(
            'rest', '--method', 'put', '--url',
            "https://management.azure.com${seedAdminId}?api-version=2024-04-13",
            '--body', '@.azure\seed-admin.json'
        )
    }
    finally {
        Remove-Item -LiteralPath '.azure\seed-admin.json' -ErrorAction SilentlyContinue
    }
}
else {
    throw 'Multiple matching seeding assignments require explicit review.'
}
$outputs | Add-Member -NotePropertyName seedAdminResourceId -NotePropertyValue @{
    type = 'String'
    value = $seedAdminId
}
$outputs | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath '.azure\deployment.json' -Encoding utf8

$insights = Invoke-AzJson @('resource', 'show', '--ids', $outputs.insightsResourceId.value, '--api-version', '2020-02-02')
$config = @(
    "FOUNDRY_PROJECT_ENDPOINT=$($outputs.projectEndpoint.value)"
    "FOUNDRY_MODEL_NAME=$($outputs.modelName.value)"
    'FOUNDRY_AGENT_NAME=security-kql-demo'
    "FOUNDRY_PROJECT_RESOURCE_ID=$($outputs.projectResourceId.value)"
    "AZURE_TENANT_ID=$($account.tenantId)"
    "ADX_CLUSTER_URI=$($outputs.clusterUri.value)"
    "ADX_DATABASE=$($outputs.databaseName.value)"
    "APPLICATIONINSIGHTS_CONNECTION_STRING=$($insights.properties.ConnectionString)"
    "APPLICATIONINSIGHTS_RESOURCE_ID=$($outputs.insightsResourceId.value)"
    "AZURE_RESOURCE_GROUP=$ResourceGroup"
    "AZURE_SUBSCRIPTION_ID=$($account.id)"
    "LOG_ANALYTICS_WORKSPACE_ID=$($outputs.workspaceId.value)"
    'DEMO_STATE_DIR=.state'
)
$config | Set-Content -LiteralPath '.env' -Encoding utf8
Write-Host 'Deployment complete. Local configuration is in the ignored .env file.'
Write-Host 'Seed the fixtures, then remove the temporary seeding permission before starting the app.'
