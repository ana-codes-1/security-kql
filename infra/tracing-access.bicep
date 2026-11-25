param insightsName string
param projectResourceId string
param projectPrincipalId string

var readerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')

resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: insightsName
}

resource projectTraceReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insights.id, projectResourceId, readerRole)
  scope: insights
  properties: {
    roleDefinitionId: readerRole
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}
