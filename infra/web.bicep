param location string = resourceGroup().location

var suffix = uniqueString(resourceGroup().id)
var accountName = 'fskql-${suffix}'
var appName = 'security-kql-${suffix}'
var aiUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var modelUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: 'log-fskql-${suffix}'
}
resource cluster 'Microsoft.Kusto/clusters@2024-04-13' existing = {
  name: 'fskql${suffix}'
}
resource database 'Microsoft.Kusto/clusters/databases@2024-04-13' existing = {
  parent: cluster
  name: 'SecurityDemo'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${appName}'
  location: location
  tags: { purpose: 'forescout-kql-demo' }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'fskql${suffix}web'
  location: location
  tags: { purpose: 'forescout-kql-demo' }
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'env-${appName}'
  location: location
  tags: { purpose: 'forescout-kql-demo' }
  properties: {
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}
resource runtimePull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRole)
  scope: registry
  properties: {
    roleDefinitionId: acrPullRole
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource runtimeAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, identity.id, aiUserRole)
  scope: account
  properties: {
    roleDefinitionId: aiUserRole
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource runtimeModelUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, identity.id, modelUserRole)
  scope: account
  properties: {
    roleDefinitionId: modelUserRole
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource runtimeViewer 'Microsoft.Kusto/clusters/databases/principalAssignments@2024-04-13' = {
  parent: database
  name: 'hosted-runtime-viewer'
  properties: {
    principalId: identity.properties.principalId
    principalType: 'App'
    role: 'Viewer'
    tenantId: tenant().tenantId
  }
}

output registryName string = registry.name
output environmentName string = environment.name
output principalId string = identity.properties.principalId
