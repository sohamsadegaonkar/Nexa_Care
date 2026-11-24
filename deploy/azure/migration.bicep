targetScope = 'resourceGroup'

@minLength(64)
@maxLength(64)
param apiDigest string

@minLength(32)
@maxLength(32)
@description('Existing Key Vault migration database URL secret version ID.')
param migrationSecretVersion string

var suffix = uniqueString(resourceGroup().id)
var prefix = 'nexa-stg-${suffix}'
var secretReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource appEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' existing = {
  name: prefix
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: 'nexastg${suffix}'
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = {
  name: prefix
}

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: prefix
}

resource vaultSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = {
  parent: vault
  name: 'db-migration-url'
}

resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${prefix}-pull'
}

resource migratorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${prefix}-migrator'
}

resource migrationSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vaultSecret.id, migratorIdentity.id, secretReaderRole)
  scope: vaultSecret
  properties: {
    principalId: migratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: secretReaderRole
  }
}

resource migration 'Microsoft.App/jobs@2025-07-01' = {
  name: 'nexa-stg-migration'
  location: 'malaysiawest'
  tags: {
    application: 'nexa-care'
    environment: 'staging'
    dataClassification: 'synthetic-only'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${pullIdentity.id}': {}
      '${migratorIdentity.id}': {}
    }
  }
  properties: {
    environmentId: appEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1200
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [{
        server: registry.properties.loginServer
        identity: pullIdentity.id
      }]
      identitySettings: [
        { identity: pullIdentity.id, lifecycle: 'None' }
        { identity: migratorIdentity.id, lifecycle: 'None' }
      ]
      secrets: [{
        name: 'migration-url'
        keyVaultUrl: '${vault.properties.vaultUri}secrets/${vaultSecret.name}/${migrationSecretVersion}'
        identity: migratorIdentity.id
      }]
    }
    template: {
      containers: [{
        name: 'migrate'
        image: '${registry.properties.loginServer}/nexa-api@sha256:${apiDigest}'
        command: ['python', '/app/scripts/run_azure_staging_migrations.py']
        env: [
          { name: 'ENVIRONMENT', value: 'staging' }
          { name: 'DATABASE_SSL_CA_PATH', value: '/etc/ssl/certs/ca-certificates.crt' }
          { name: 'DATABASE_ECHO_SQL', value: 'false' }
          { name: 'NEXA_AZURE_STAGING_POSTGRES_HOST', value: postgres.properties.fullyQualifiedDomainName }
          { name: 'MIGRATION_DATABASE_URL', secretRef: 'migration-url' }
        ]
        resources: {
          cpu: json('0.5')
          memory: '1Gi'
        }
      }]
    }
  }
  dependsOn: [migrationSecretReader]
}

output manualJobName string = migration.name
