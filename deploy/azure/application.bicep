targetScope = 'resourceGroup'

@minLength(64)
@maxLength(64)
param apiDigest string

@minLength(64)
@maxLength(64)
param webDigest string

@minLength(64)
@maxLength(64)
param scannerDigest string

@description('IPv4 addresses for approved staging testers or an approved staging access gateway. Each rule is a /32.')
@minLength(1)
@maxLength(12)
param testerIpv4Addresses string[]

@description('Observed and reviewed private ACA ingress peer addresses. Each trust entry is a /32.')
@minLength(1)
@maxLength(12)
param proxyIpv4Addresses string[]

@description('Approved synthetic-only Supabase Auth HTTPS origin.')
param supabaseOrigin string

@description('Dedicated AWS sandbox role with a qualified staging OIDC trust.')
param awsRoleArn string

@description('Entra resource application ID URI for the AWS federation audience.')
param awsTokenResource string

@description('Synthetic-only S3 bucket in ap-south-1.')
param documentBucket string
param documentKmsKeyArn string
param patientKmsKeyArn string
param catalogSigningKeyArn string

@description('Approved patient-facing policy copies and digests for the staging identity flow.')
param patientLegalConfig {
  termsVersion: string
  termsUrl: string
  termsSha256: string
  privacyVersion: string
  privacyUrl: string
  privacySha256: string
}

@description('Confirmed 32-character version IDs by secret name. Supply identifiers only.')
param secretVersions object

var location = 'centralindia'
var suffix = uniqueString(resourceGroup().id)
var prefix = 'nexa-stg-${suffix}'
var tags = {
  application: 'nexa-care'
  environment: 'staging'
  dataClassification: 'synthetic-only'
}
var webName = 'nexa-stg-web'
var apiName = 'nexa-stg-api'
var domain = appEnvironment.properties.defaultDomain
var webHost = '${webName}.${domain}'
var apiHost = '${apiName}.internal.${domain}'
var webOrigin = 'https://${webHost}'
var apiOrigin = 'https://${apiHost}'
var proxyNetworks = join(map(proxyIpv4Addresses, address => '${address}/32'), ',')

resource appEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' existing = {
  name: prefix
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: 'nexastg${suffix}'
}

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: prefix
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = {
  name: prefix
}

resource redis 'Microsoft.Cache/redisEnterprise@2025-07-01' existing = {
  name: prefix
}

resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${prefix}-pull'
}

resource secretsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${prefix}-api-secrets'
}

resource workloadIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${prefix}-api-workload'
}

var apiSecrets = {
  'db-runtime-url': 'DATABASE_URL'
  'redis-tls-url': 'UPSTASH_REDIS_URL'
  'supabase-service-key': 'SUPABASE_KEY'
  'handshake-pepper': 'HANDSHAKE_PEPPER_SECRET'
  'mfa-encryption-key': 'MFA_ENCRYPTION_KEY'
  'pii-encryption-key': 'PII_ENCRYPTION_KEY'
  'patient-jwt-secret': 'PATIENT_JWT_SECRET'
  'otp-rate-limit-hmac': 'OTP_RATE_LIMIT_HMAC_SECRET'
  'registration-idempotency-hmac': 'PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET'
  'contact-assurance-hmac': 'PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET'
  'patient-grant-reference-hmac': 'PATIENT_GRANT_REFERENCE_HMAC_SECRET'
  'operations-token': 'OPERATIONS_AUTH_TOKEN'
  'document-encryption-key': 'DOCUMENT_STORAGE_ENCRYPTION_KEY'
  'staging-otp-phones': 'STAGING_OTP_PHONE_ALLOWLIST'
}
var secretNames = items(apiSecrets)
var secretReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var registryConfig = [{
  server: registry.properties.loginServer
  identity: pullIdentity.id
}]

resource apiVaultSecrets 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = [for entry in secretNames: {
  parent: vault
  name: entry.key
}]

resource apiSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (entry, index) in secretNames: {
  name: guid(apiVaultSecrets[index].id, secretsIdentity.id, secretReaderRole)
  scope: apiVaultSecrets[index]
  properties: {
    principalId: secretsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: secretReaderRole
  }
}]

var apiConfig = {
  ENVIRONMENT: 'staging'
  PORT: '8000'
  UVICORN_ACCESS_LOG: 'false'
  DATABASE_SSL_CA_PATH: '/etc/ssl/certs/ca-certificates.crt'
  DATABASE_ECHO_SQL: 'false'
  NEXA_AZURE_STAGING_POSTGRES_HOST: postgres.properties.fullyQualifiedDomainName
  NEXA_AZURE_STAGING_REDIS_HOST: redis.properties.hostName
  SUPABASE_URL: supabaseOrigin
  TRUSTED_HOSTS: apiHost
  CORS_ALLOWED_ORIGINS: webOrigin
  FORWARDED_ALLOW_IPS: proxyNetworks
  TRUSTED_PROXY_NETWORKS: proxyNetworks
  PUSH_STATUS_TRANSPORT: 'poll'
  AUTO_COMMIT: 'false'
  MAX_UPLOAD_BYTES: '10485760'
  PATIENT_SOURCE_MALWARE_SCANNER: 'clamd'
  PATIENT_SOURCE_CLAMD_HOST: '127.0.0.1'
  PATIENT_SOURCE_CLAMD_PORT: '3310'
  PATIENT_SOURCE_CLAMD_MAX_BYTES: '10485760'
  AWS_REGION: 'ap-south-1'
  AWS_DEFAULT_REGION: 'ap-south-1'
  AWS_PROFILE: 'nexa-azure-staging'
  AWS_CONFIG_FILE: '/app/deploy/azure/aws-staging-config'
  AWS_EC2_METADATA_DISABLED: 'true'
  NEXA_AZURE_AWS_ROLE_ARN: awsRoleArn
  NEXA_AZURE_AWS_TOKEN_RESOURCE: awsTokenResource
  NEXA_AZURE_AWS_IDENTITY_CLIENT_ID: workloadIdentity.properties.clientId
  PATIENT_TERMS_VERSION: patientLegalConfig.termsVersion
  PATIENT_TERMS_URL: patientLegalConfig.termsUrl
  PATIENT_TERMS_SHA256: patientLegalConfig.termsSha256
  PATIENT_PRIVACY_VERSION: patientLegalConfig.privacyVersion
  PATIENT_PRIVACY_URL: patientLegalConfig.privacyUrl
  PATIENT_PRIVACY_SHA256: patientLegalConfig.privacySha256
  DOCUMENT_STORAGE_PROVIDER: 's3'
  DOCUMENT_STORAGE_S3_BUCKET: documentBucket
  DOCUMENT_STORAGE_S3_REGION: 'ap-south-1'
  DOCUMENT_STORAGE_S3_KMS_KEY_ID: documentKmsKeyArn
  ENCRYPTION_BACKEND: 'kms'
  AWS_PATIENT_SPECIFIC_KMS_KEYS: 'false'
  KMS_KEY_ID: patientKmsKeyArn
  MEDICATION_CATALOG_SIGNING_KEY_ID: catalogSigningKeyArn
  DOCUMENT_EXTRACTION_PROVIDER: 'aws_textract'
  DOCUMENT_AI_AWS_REGION: 'ap-south-1'
}

resource api 'Microsoft.App/containerApps@2025-07-01' = {
  name: apiName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${pullIdentity.id}': {}
      '${secretsIdentity.id}': {}
      '${workloadIdentity.id}': {}
    }
  }
  properties: {
    environmentId: appEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      identitySettings: [
        { identity: pullIdentity.id, lifecycle: 'None' }
        { identity: secretsIdentity.id, lifecycle: 'None' }
        { identity: workloadIdentity.id, lifecycle: 'Main' }
      ]
      registries: registryConfig
      secrets: [for entry in secretNames: {
        name: entry.key
        keyVaultUrl: '${vault.properties.vaultUri}secrets/${entry.key}/${secretVersions[entry.key]}'
        identity: secretsIdentity.id
      }]
      ingress: {
        external: false
        targetPort: 8000
        allowInsecure: false
        transport: 'auto'
      }
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
      containers: [
        {
          name: 'api'
          image: '${registry.properties.loginServer}/nexa-api@sha256:${apiDigest}'
          command: ['python', '/app/scripts/start_azure_staging_api.py']
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(map(items(apiConfig), entry => {
            name: entry.key
            value: entry.value
          }), map(secretNames, entry => {
            name: entry.value
            secretRef: entry.key
          }))
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/healthz', port: 8000, httpHeaders: [{ name: 'Host', value: apiHost }] }
              periodSeconds: 5
              failureThreshold: 60
            }
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000, httpHeaders: [{ name: 'Host', value: apiHost }] }
              periodSeconds: 15
              timeoutSeconds: 5
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000, httpHeaders: [{ name: 'Host', value: apiHost }] }
              periodSeconds: 15
              timeoutSeconds: 5
            }
          ]
        }
        {
          name: 'clamd'
          image: '${registry.properties.loginServer}/nexa-clamd@sha256:${scannerDigest}'
          resources: {
            cpu: json('1.5')
            memory: '3Gi'
          }
          env: [
            { name: 'CLAMD_CONF_TCPAddr', value: '127.0.0.1' }
            { name: 'CLAMD_CONF_ConcurrentDatabaseReload', value: 'no' }
            { name: 'CLAMD_CONF_StreamMaxLength', value: '12M' }
            { name: 'FRESHCLAM_CHECKS', value: '2' }
          ]
        }
      ]
    }
  }
  dependsOn: [apiSecretReader]
}

resource web 'Microsoft.App/containerApps@2025-07-01' = {
  name: webName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${pullIdentity.id}': {}
    }
  }
  properties: {
    environmentId: appEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      identitySettings: [{ identity: pullIdentity.id, lifecycle: 'None' }]
      activeRevisionsMode: 'Single'
      registries: registryConfig
      ingress: {
        external: true
        targetPort: 3000
        allowInsecure: false
        transport: 'auto'
        ipSecurityRestrictions: [for (address, index) in testerIpv4Addresses: {
          name: 'staging-tester-${index}'
          ipAddressRange: '${address}/32'
          action: 'Allow'
        }]
      }
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
      containers: [{
        name: 'web'
        image: '${registry.properties.loginServer}/nexa-web@sha256:${webDigest}'
        resources: {
          cpu: json('0.5')
          memory: '1Gi'
        }
        env: [
          { name: 'NEXA_AZURE_STAGING_RUNTIME', value: '1' }
          { name: 'NEXA_AZURE_STAGING_EXPECTED_API_ORIGIN', value: apiOrigin }
          { name: 'NEXA_AZURE_STAGING_EXPECTED_WEB_ORIGIN', value: webOrigin }
        ]
        probes: [
          {
            type: 'Startup'
            httpGet: { path: '/healthz', port: 3000 }
            periodSeconds: 5
            failureThreshold: 60
          }
          {
            type: 'Liveness'
            httpGet: { path: '/healthz', port: 3000 }
            periodSeconds: 15
          }
          {
            type: 'Readiness'
            httpGet: { path: '/readyz', port: 3000 }
            periodSeconds: 15
            timeoutSeconds: 8
          }
        ]
      }]
    }
  }
  dependsOn: [api]
}

output webOrigin string = webOrigin
output apiOrigin string = apiOrigin
