import type { ConfigContext, ExpoConfig } from 'expo/config'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

function environment(): 'development' | 'preview' | 'production' {
  const value = process.env.EXPO_PUBLIC_APP_ENV ?? process.env.EAS_BUILD_PROFILE
  if (value === 'preview' || value === 'production') return value
  return 'development'
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const appEnvironment = environment()
  const allowHttp = process.env.EXPO_PUBLIC_ALLOW_HTTP === 'true'
  const easProjectId = process.env.EXPO_PUBLIC_EAS_PROJECT_ID?.trim()
  const configuredGoogleServicesFile = process.env.GOOGLE_SERVICES_FILE?.trim()
  const googleServicesFile = configuredGoogleServicesFile
    ? resolve(__dirname, configuredGoogleServicesFile)
    : undefined

  if (allowHttp && appEnvironment !== 'development') {
    throw new Error('EXPO_PUBLIC_ALLOW_HTTP=true is only permitted for development builds.')
  }
  if (appEnvironment !== 'development' && (!easProjectId || !googleServicesFile)) {
    throw new Error(
      'Preview and production builds require EXPO_PUBLIC_EAS_PROJECT_ID and GOOGLE_SERVICES_FILE from the EAS environment.',
    )
  }
  if (googleServicesFile && !existsSync(googleServicesFile)) {
    throw new Error('GOOGLE_SERVICES_FILE does not resolve to an available Firebase client configuration.')
  }
  if (appEnvironment === 'production' && googleServicesFile) {
    const firebaseConfig = JSON.parse(readFileSync(googleServicesFile, 'utf8')) as {
      project_info?: { project_id?: string }
    }
    if (firebaseConfig.project_info?.project_id?.toLowerCase().includes('alpha')) {
      throw new Error('Production builds must not use an alpha Firebase project.')
    }
  }

  return {
    ...config,
    name: config.name ?? 'Nexa Care',
    slug: config.slug ?? 'nexa-care',
    plugins: [
      ...(config.plugins ?? []),
      [
        'expo-build-properties',
        {
          android: {
            usesCleartextTraffic: allowHttp,
          },
        },
      ],
    ],
    android: {
      ...config.android,
      package: config.android?.package ?? 'ai.nexacare.patient',
      ...(googleServicesFile ? { googleServicesFile } : {}),
    },
    extra: {
      ...config.extra,
      appEnvironment,
      eas: easProjectId ? { projectId: easProjectId } : undefined,
    },
  }
}
