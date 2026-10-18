@maxLength(20)
@minLength(4)
@description('Used to generate names for all resources in this file')
param resourceBaseName string

@description('Microsoft Foundry project endpoint used by the agent')
param azureAiProjectEndpoint string
@description('Model deployment in the Microsoft Foundry project')
param azureAiModelDeploymentName string
param graphHistoryMessageCount string = '10'
param handoffHumanUpn string = ''
param handoffTenantId string = ''
param handoffBrokerEnabled string = 'false'
param handoffHumanObjectId string = ''
param handoffTeamsCatalogAppId string = ''
@description('Optional registered Entra app ID for Graph RSC; configure its managed-identity federation separately.')
param graphClientId string = ''

param webAppSKU string
param linuxFxVersion string

@maxLength(42)
param botDisplayName string

param serverfarmsName string = resourceBaseName
param webAppName string = resourceBaseName
param identityName string = resourceBaseName
param location string = resourceGroup().location
param pythonVersion string = linuxFxVersion

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  location: location
  name: identityName
}

// Compute resources for your Web App
resource serverfarm 'Microsoft.Web/serverfarms@2021-02-01' = {
  kind: 'app,linux'
  location: location
  name: serverfarmsName
  sku: {
    name: webAppSKU
  }
  properties:{
    reserved: true
  }
}

// Web App that hosts your agent
resource webApp 'Microsoft.Web/sites@2021-02-01' = {
  kind: 'app,linux'
  location: location
  name: webAppName
  properties: {
    serverFarmId: serverfarm.id
    siteConfig: {
      alwaysOn: true
      appCommandLine: 'gunicorn --bind 0.0.0.0 --workers 1 --worker-class aiohttp.worker.GunicornWebWorker --timeout 600 app:app'
      linuxFxVersion: pythonVersion
      appSettings: [
        {
          name: 'WEBSITES_CONTAINER_START_TIME_LIMIT'
          value: '600'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
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
          name: 'GRAPH_AUTH_MODE'
          value: empty(graphClientId) ? 'default' : 'federated'
        }
        {
          name: 'GRAPH_CLIENT_ID'
          value: graphClientId
        }
        {
          name: 'GRAPH_TENANT_ID'
          value: identity.properties.tenantId
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
          name: 'GRAPH_HISTORY_MESSAGE_COUNT'
          value: graphHistoryMessageCount
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
        {
          name: 'BOT_TENANT_ID'
          value: identity.properties.tenantId
        }
        { 
          name: 'BOT_TYPE'
          value: 'UserAssignedMsi' 
        }
      ]
      ftpsState: 'FtpsOnly'
    }
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
}

// Register your web service as a bot with the Bot Framework
module azureBotRegistration './botRegistration/azurebot.bicep' = {
  name: 'Azure-Bot-registration'
  params: {
    resourceBaseName: resourceBaseName
    identityClientId: identity.properties.clientId
    identityResourceId: identity.id
    identityTenantId: identity.properties.tenantId
    botAppDomain: webApp.properties.defaultHostName
    botDisplayName: botDisplayName
  }
}

// The output will be persisted in .env.{envName}. Visit https://aka.ms/teamsfx-actions/arm-deploy for more details.
output BOT_AZURE_APP_SERVICE_RESOURCE_ID string = webApp.id
output BOT_DOMAIN string = webApp.properties.defaultHostName
output BOT_ID string = identity.properties.clientId
output BOT_TENANT_ID string = identity.properties.tenantId
output CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID string = identity.properties.clientId
output CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE string = 'UserManagedIdentity'
output CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID string = identity.properties.tenantId
