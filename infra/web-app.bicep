param location string = resourceGroup().location
param modelName string = 'gpt-5-mini'
param agentName string = 'security-kql-demo'
param imageTag string
param revisionSuffix string = imageTag
@secure()
param referenceApproval string

var suffix = uniqueString(resourceGroup().id)
var accountName = 'fskql-${suffix}'
var appName = 'security-kql-${suffix}'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: 'security-kql'
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: 'appi-fskql-${suffix}'
}
resource cluster 'Microsoft.Kusto/clusters@2024-04-13' existing = {
  name: 'fskql${suffix}'
}
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: 'env-${appName}'
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-${appName}'
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: 'fskql${suffix}web'
}

resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
  tags: { purpose: 'forescout-kql-demo' }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        { server: registry.properties.loginServer, identity: identity.id }
      ]
      secrets: [
        { name: 'telemetry-connection', value: insights.properties.ConnectionString }
        { name: 'reference-approval', value: referenceApproval }
      ]
    }
    template: {
      revisionSuffix: revisionSuffix
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [
        {
          name: 'web'
          image: '${registry.properties.loginServer}/security-kql-demo:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'DEMO_PUBLIC', value: 'true' }
            { name: 'DEMO_HOSTNAME', value: '${appName}.${environment.properties.defaultDomain}' }
            { name: 'DEMO_MANAGED_IDENTITY', value: 'true' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'DEMO_STATE_DIR', value: '/data' }
            { name: 'DEMO_REFERENCE_APPROVAL', secretRef: 'reference-approval' }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${accountName}.services.ai.azure.com/api/projects/security-kql' }
            { name: 'FOUNDRY_MODEL_NAME', value: modelName }
            { name: 'FOUNDRY_AGENT_NAME', value: agentName }
            { name: 'FOUNDRY_PROJECT_RESOURCE_ID', value: project.id }
            { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
            { name: 'ADX_CLUSTER_URI', value: cluster.properties.uri }
            { name: 'ADX_DATABASE', value: 'SecurityDemo' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'telemetry-connection' }
            { name: 'APPLICATIONINSIGHTS_RESOURCE_ID', value: insights.id }
          ]
        }
      ]
    }
  }
}

output appName string = app.name
output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output appResourceId string = app.id
