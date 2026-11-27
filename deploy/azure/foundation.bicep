targetScope = 'resourceGroup'

@description('One-time administrator password. Use a protected secure parameter file. Never use this principal at runtime.')
@secure()
param postgresAdministratorPassword string

@description('Azure Managed Redis HA is optional for this synthetic stage. Reprice before enabling it.')
param redisHighAvailability bool = false

var location = 'malaysiawest'
var suffix = uniqueString(resourceGroup().id)
var prefix = 'nexa-stg-${suffix}'
var tags = {
  application: 'nexa-care'
  environment: 'staging'
  dataClassification: 'synthetic-only'
}
var appsSubnetName = 'container-apps'
var postgresSubnetName = 'postgres'
var endpointsSubnetName = 'private-endpoints'
var postgresDnsZoneName = 'nexa-stg.postgres.database.azure.com'
var appsSubnetId = resourceId('Microsoft.Network/virtualNetworks/subnets', network.name, appsSubnetName)
var postgresSubnetId = resourceId('Microsoft.Network/virtualNetworks/subnets', network.name, postgresSubnetName)
var endpointsSubnetId = resourceId('Microsoft.Network/virtualNetworks/subnets', network.name, endpointsSubnetName)

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.42.0.0/23']
    }
    subnets: [
      {
        name: appsSubnetName
        properties: {
          addressPrefix: '10.42.0.0/25'
          delegations: [{
            name: 'container-apps'
            properties: {
              serviceName: 'Microsoft.App/environments'
            }
          }]
        }
      }
      {
        name: postgresSubnetName
        properties: {
          addressPrefix: '10.42.1.0/27'
          delegations: [{
            name: 'postgres'
            properties: {
              serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
            }
          }]
        }
      }
      {
        name: endpointsSubnetName
        properties: {
          addressPrefix: '10.42.1.32/27'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource postgresDns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: postgresDnsZoneName
  location: 'global'
  tags: tags
}

resource postgresDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: postgresDns
  name: 'staging'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: network.id
    }
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: prefix
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: 'nexa_stg_admin'
    administratorLoginPassword: postgresAdministratorPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 14
      geoRedundantBackup: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: postgresSubnetId
      privateDnsZoneArmResourceId: postgresDns.id
      publicNetworkAccess: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
  dependsOn: [postgresDnsLink]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: 'nexacare_staging'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource postgresTls 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: postgres
  name: 'require_secure_transport'
  properties: {
    value: 'on'
    source: 'user-override'
  }
}

resource postgresExtensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: {
    value: 'PGCRYPTO'
    source: 'user-override'
  }
}

resource redis 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  name: prefix
  location: location
  tags: tags
  sku: {
    name: 'Balanced_B0'
  }
  properties: {
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
    highAvailability: redisHighAvailability ? 'Enabled' : 'Disabled'
  }
}

resource redisDatabase 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  parent: redis
  name: 'default'
  properties: {
    accessKeysAuthentication: 'Enabled'
    clientProtocol: 'Encrypted'
    clusteringPolicy: 'NoCluster'
    evictionPolicy: 'NoEviction'
    port: 10000
  }
}

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
  }
}

resource evidence 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: 'nexastg${suffix}'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  tags: tags
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: evidence
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: {
      enabled: true
      days: 14
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 14
    }
  }
}

resource restoreEvidence 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobService
  name: 'nexa-staging-recovery'
  properties: {
    publicAccess: 'None'
  }
}

var linkTargets = [
  { name: 'vault', id: vault.id, group: 'vault', zone: 'privatelink.vaultcore.azure.net' }
  { name: 'redis', id: redis.id, group: 'redisEnterprise', zone: 'privatelink.redis.azure.net' }
  { name: 'blob', id: evidence.id, group: 'blob', zone: 'privatelink.blob.${environment().suffixes.storage}' }
]

resource privateDns 'Microsoft.Network/privateDnsZones@2024-06-01' = [for link in linkTargets: {
  name: link.zone
  location: 'global'
  tags: tags
}]

resource privateDnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (link, index) in linkTargets: {
  parent: privateDns[index]
  name: 'staging'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: network.id
    }
  }
}]

resource endpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [for link in linkTargets: {
  name: '${prefix}-${link.name}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: endpointsSubnetId
    }
    privateLinkServiceConnections: [{
      name: link.name
      properties: {
        privateLinkServiceId: link.id
        groupIds: [link.group]
      }
    }]
  }
}]

resource endpointDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (link, index) in linkTargets: {
  parent: endpoints[index]
  name: 'staging'
  properties: {
    privateDnsZoneConfigs: [{
      name: link.name
      properties: {
        privateDnsZoneId: privateDns[index].id
      }
    }]
  }
}]

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: 'nexastg${suffix}'
  location: location
  sku: {
    name: 'Basic'
  }
  tags: tags
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
    policies: {
      azureADAuthenticationAsArmPolicy: {
        status: 'enabled'
      }
    }
  }
}

var identityNames = [
  'pull'
  'api-secrets'
  'migrator'
  'api-workload'
]

resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for name in identityNames: {
  name: '${prefix}-${name}'
  location: location
  tags: tags
}]

var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identities[0].id, acrPullRole)
  scope: registry
  properties: {
    principalId: identities[0].properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: 1
    }
    features: {
      disableLocalAuth: true
      enableLogAccessUsingOnlyResourcePermissions: false
    }
  }
}

resource appsEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: appsSubnetId
      internal: false
    }
    workloadProfiles: [{
      name: 'Consumption'
      workloadProfileType: 'Consumption'
    }]
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    peerAuthentication: {
      mtls: {
        enabled: true
      }
    }
    peerTrafficConfiguration: {
      encryption: {
        enabled: true
      }
    }
  }
}

resource appDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: appsEnvironment
  name: 'system-staging'
  properties: {
    workspaceId: logs.id
    logs: [{
      category: 'ContainerAppSystemLogs'
      enabled: true
    }]
  }
}

resource vaultDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: vault
  name: 'vault-staging'
  properties: {
    workspaceId: logs.id
    logs: [{
      category: 'AuditEvent'
      enabled: true
    }]
  }
}

output environmentName string = appsEnvironment.name
output webOrigin string = 'https://nexa-stg-web.${appsEnvironment.properties.defaultDomain}'
output apiOrigin string = 'https://nexa-stg-api.internal.${appsEnvironment.properties.defaultDomain}'
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output databaseName string = database.name
output redisHost string = redis.properties.hostName
output registryName string = registry.name
output vaultName string = vault.name
output evidenceStorageName string = evidence.name
output evidenceContainer string = restoreEvidence.name
output apiWorkloadClientId string = identities[3].properties.clientId
