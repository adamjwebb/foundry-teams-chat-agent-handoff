@minLength(4)
@maxLength(20)
param resourceBaseName string
param location string = resourceGroup().location
param azureAiProjectEndpoint string
param azureAiModelDeploymentName string
param botDisplayName string
param imageTag string = 'handoff-demo'
param deployApp bool = true
param handoffHumanUpn string = ''
param handoffTenantId string = ''
param handoffBrokerEnabled string = 'false'
param handoffHumanObjectId string = ''
param handoffTeamsCatalogAppId string = ''

var registryName = 'handoff${uniqueString(subscription().id, resourceGroup().id)}'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: resourceBaseName
  location: location
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'AcrPull')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: '${resourceBaseName}-env'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' = if (deployApp) {
  name: resourceBaseName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  dependsOn: [
    registryPull
  ]
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'bot'
          image: '${registry.properties.loginServer}/teams-handoff:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Startup'
              tcpSocket: {
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'Readiness'
              tcpSocket: {
                port: 8000
              }
              periodSeconds: 10
            }
          ]
          env: [
            {
              name: 'BOT_ID'
              value: identity.properties.clientId
            }
            {
              name: 'CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID'
              value: identity.properties.clientId
            }
            {
              name: 'CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE'
              value: 'UserManagedIdentity'
            }
            {
              name: 'CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID'
              value: identity.properties.tenantId
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identity.properties.clientId
            }
            {
              name: 'AZURE_AI_PROJECT_ENDPOINT'
              value: azureAiProjectEndpoint
            }
            {
              name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
              value: azureAiModelDeploymentName
            }
            {
              name: 'HANDOFF_HUMAN_UPN'
              value: handoffHumanUpn
            }
            {
              name: 'HANDOFF_TENANT_ID'
              value: handoffTenantId
            }
            {
              name: 'HANDOFF_BROKER_ENABLED'
              value: handoffBrokerEnabled
            }
            {
              name: 'HANDOFF_HUMAN_OBJECT_ID'
              value: handoffHumanObjectId
            }
            {
              name: 'HANDOFF_TEAMS_CATALOG_APP_ID'
              value: handoffTeamsCatalogAppId
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

module bot './botRegistration/azurebot.bicep' = if (deployApp) {
  name: 'Container-App-Bot-registration'
  params: {
    resourceBaseName: resourceBaseName
    botDisplayName: botDisplayName
    identityClientId: identity.properties.clientId
    identityResourceId: identity.id
    identityTenantId: identity.properties.tenantId
    botAppDomain: containerApp!.properties.configuration!.ingress!.fqdn!
  }
}

output REGISTRY_NAME string = registry.name
output REGISTRY_LOGIN_SERVER string = registry.properties.loginServer
output BOT_ID string = identity.properties.clientId
output BOT_PRINCIPAL_ID string = identity.properties.principalId
output BOT_TENANT_ID string = identity.properties.tenantId
output BOT_DOMAIN string = deployApp ? containerApp!.properties.configuration!.ingress!.fqdn! : ''
