import type { ConfigContext, ExpoConfig } from 'expo/config'

function environment(): 'development' | 'preview' | 'production' {
  const value = process.env.EXPO_PUBLIC_APP_ENV ?? process.env.EAS_BUILD_PROFILE
  if (value === 'preview' || value === 'production') return value
  return 'development'
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const appEnvironment = environment()
  const allowHttp = process.env.EXPO_PUBLIC_ALLOW_HTTP === 'true'

  if (allowHttp && appEnvironment !== 'development') {
    throw new Error('EXPO_PUBLIC_ALLOW_HTTP=true is only permitted for development builds.')
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
    extra: {
      ...config.extra,
      appEnvironment,
    },
  }
}
