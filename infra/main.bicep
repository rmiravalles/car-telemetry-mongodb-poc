targetScope = 'resourceGroup'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Prefix used to name resources')
@minLength(3)
@maxLength(11)
param namePrefix string

@description('Event Hub name for telemetry ingestion')
param eventHubName string = 'vehicle-telemetry'

@description('Number of partitions in Event Hub')
@minValue(1)
@maxValue(32)
param eventHubPartitions int = 4

@description('ADLS Gen2 file system for raw telemetry files')
param dataLakeFileSystem string = 'raw-telemetry'

var storageAccountName = toLower('${namePrefix}dls${uniqueString(resourceGroup().id)}')
var eventHubNamespaceName = toLower('${namePrefix}ehns')
var appServicePlanName = '${namePrefix}-func-plan'
var functionAppName = '${namePrefix}-func'
var appInsightsName = '${namePrefix}-appi'
var logAnalyticsName = '${namePrefix}-log'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    isHnsEnabled: true
    accessTier: 'Hot'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource fileSystem 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: dataLakeFileSystem
  properties: {
    publicAccess: 'None'
  }
}

resource eventHubNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: eventHubNamespaceName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
    capacity: 1
  }
  properties: {
    isAutoInflateEnabled: true
    maximumThroughputUnits: 4
    minimumTlsVersion: '1.2'
  }
}

resource eventHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubNamespace
  name: eventHubName
  properties: {
    messageRetentionInDays: 1
    partitionCount: eventHubPartitions
    status: 'Active'
  }
}

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  sku: {
    tier: 'Dynamic'
    name: 'Y1'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'DATA_LAKE_ACCOUNT_URL'
          value: 'https://${storage.name}.dfs.core.windows.net'
        }
        {
          name: 'DATA_LAKE_FILE_SYSTEM'
          value: dataLakeFileSystem
        }
        {
          name: 'EVENT_HUB_NAME'
          value: eventHubName
        }
        {
          name: 'EVENT_HUB_CONSUMER_GROUP'
          value: '$Default'
        }
        {
          name: 'EventHubConnection__fullyQualifiedNamespace'
          value: '${eventHubNamespace.name}.servicebus.windows.net'
        }
      ]
    }
  }
}

resource eventHubReceiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(eventHubNamespace.id, functionApp.identity.principalId, 'eventhub-receiver')
  scope: eventHubNamespace
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde')
    principalType: 'ServicePrincipal'
  }
}

resource storageBlobContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.identity.principalId, 'storage-blob-contributor')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalType: 'ServicePrincipal'
  }
}

output functionAppName string = functionApp.name
output eventHubNamespaceFqdn string = '${eventHubNamespace.name}.servicebus.windows.net'
output eventHubNameOut string = eventHub.name
output dataLakeAccountUrl string = 'https://${storage.name}.dfs.core.windows.net'
output dataLakeFileSystemOut string = fileSystem.name
output functionPrincipalId string = functionApp.identity.principalId
