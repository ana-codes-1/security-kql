param location string = resourceGroup().location
param operatorPrincipalId string
param modelName string = 'gpt-5-mini'
param modelVersion string = '2025-08-07'

var suffix = uniqueString(resourceGroup().id)
var accountName = 'fskql-${suffix}'
var clusterName = 'fskql${suffix}'
var projectName = 'security-kql'
var databaseName = 'SecurityDemo'
var aiUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var modelUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-fskql-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-fskql-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Security KQL - observability demo'
    description: 'Synthetic security data, guarded KQL, and exact-result evaluation.'
  }
  // Model and project updates cannot run concurrently against the same account.
  dependsOn: [model]
}

resource insightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'application-insights'
  properties: {
    category: 'AppInsights'
    authType: 'ApiKey'
    target: insights.id
    credentials: {
      key: insights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: insights.id
    }
  }
}

module tracingAccess 'tracing-access.bicep' = {
  name: 'security-kql-tracing-access'
  params: {
    insightsName: insights.name
    projectResourceId: project.id
    projectPrincipalId: project.identity.principalId
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: modelName
  sku: {
    name: 'GlobalStandard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource operatorAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, operatorPrincipalId, aiUserRole)
  scope: account
  properties: {
    roleDefinitionId: aiUserRole
    principalId: operatorPrincipalId
    principalType: 'User'
  }
}

resource operatorModelUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, operatorPrincipalId, modelUserRole)
  scope: account
  properties: {
    roleDefinitionId: modelUserRole
    principalId: operatorPrincipalId
    principalType: 'User'
  }
}

resource projectModelUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, modelUserRole)
  scope: account
  properties: {
    roleDefinitionId: modelUserRole
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource cluster 'Microsoft.Kusto/clusters@2024-04-13' = {
  name: clusterName
  location: location
  sku: {
    name: 'Dev(No SLA)_Standard_E2a_v4'
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    enableAutoStop: true
    enableDiskEncryption: true
    enablePurge: false
    enableStreamingIngest: false
    engineType: 'V2'
    publicNetworkAccess: 'Enabled'
    trustedExternalTenants: []
  }
}

resource database 'Microsoft.Kusto/clusters/databases@2024-04-13' = {
  parent: cluster
  name: databaseName
  location: location
  kind: 'ReadWrite'
  properties: {
    softDeletePeriod: 'P30D'
    hotCachePeriod: 'P7D'
  }
}

resource viewer 'Microsoft.Kusto/clusters/databases/principalAssignments@2024-04-13' = {
  parent: database
  name: 'runtime-viewer'
  properties: {
    principalId: operatorPrincipalId
    principalType: 'User'
    role: 'Viewer'
    tenantId: tenant().tenantId
  }
}

output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
output projectResourceId string = project.id
output accountName string = accountName
output modelName string = model.name
output clusterUri string = cluster.properties.uri
output clusterName string = cluster.name
output databaseName string = database.name
output insightsResourceId string = insights.id
output insightsName string = insights.name
output databaseResourceId string = database.id
output workspaceId string = workspace.properties.customerId
