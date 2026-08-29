export type AppEnvironment = 'development' | 'preview' | 'production'

export class RuntimeConfigError extends Error {
  constructor(
    message: string,
    public readonly code:
      | 'MISSING_API_BASE_URL'
      | 'INVALID_API_BASE_URL'
      | 'UNSUPPORTED_API_PROTOCOL'
      | 'INSECURE_API_URL'
  ) {
    super(message)
    this.name = 'RuntimeConfigError'
  }
}

export interface ApiUrlPolicy {
  environment: AppEnvironment
  allowHttp: boolean
  source: 'expo' | 'next'
}

function isLocalAddress(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '')
  return (
    host === 'localhost' ||
    host === '::1' ||
    host === '0.0.0.0' ||
    host.startsWith('127.') ||
    host.startsWith('10.') ||
    host.startsWith('192.168.') ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(host)
  )
}

export function resolveApiUrl(rawValue: string | undefined, policy: ApiUrlPolicy): string {
  const raw = rawValue?.trim()
  if (!raw) {
    const variable = policy.source === 'expo' ? 'EXPO_PUBLIC_API_URL' : 'NEXT_PUBLIC_API_URL'
    throw new RuntimeConfigError(
      `API base URL is not configured. Set ${variable}.`,
      'MISSING_API_BASE_URL'
    )
  }

  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    throw new RuntimeConfigError(
      'API base URL must be a valid absolute URL.',
      'INVALID_API_BASE_URL'
    )
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new RuntimeConfigError('API base URL must use HTTP or HTTPS.', 'UNSUPPORTED_API_PROTOCOL')
  }

  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new RuntimeConfigError(
      'API base URL must not contain credentials, query parameters, or a fragment.',
      'INVALID_API_BASE_URL'
    )
  }

  if (policy.environment === 'production') {
    if (parsed.protocol !== 'https:' || isLocalAddress(parsed.hostname)) {
      throw new RuntimeConfigError(
        'Production API base URL must use HTTPS and a non-local host.',
        'INSECURE_API_URL'
      )
    }
  } else if (parsed.protocol === 'http:' && !policy.allowHttp) {
    throw new RuntimeConfigError(
      'HTTP API access is disabled. Set EXPO_PUBLIC_ALLOW_HTTP=true only for intentional local development.',
      'INSECURE_API_URL'
    )
  }

  const path = parsed.pathname.replace(/\/+$/, '')
  return `${parsed.origin}${path === '/' ? '' : path}`
}

/**
 * Provider browser traffic must stay on the doctor origin so Next.js can
 * proxy `/api` requests server-side with first-party cookies. The public URL
 * remains required to fail closed when production configuration is missing,
 * but it must never point browser code directly at the backend.
 */
export function resolveNextBrowserApiUrl(
  rawValue: string | undefined,
  environment: AppEnvironment,
  browserOrigin?: string
): string {
  const resolved = resolveApiUrl(rawValue, {
    environment,
    allowHttp: environment !== 'production',
    source: 'next',
  })
  if (environment !== 'production' || !browserOrigin) return resolved

  let normalizedBrowserOrigin: string
  try {
    normalizedBrowserOrigin = new URL(browserOrigin).origin
  } catch {
    throw new RuntimeConfigError('Doctor browser origin is invalid.', 'INVALID_API_BASE_URL')
  }
  if (resolved !== normalizedBrowserOrigin) {
    throw new RuntimeConfigError(
      'Production doctor browser API URL must equal the current doctor origin.',
      'INSECURE_API_URL'
    )
  }
  return resolved
}

function expoEnvironment(): AppEnvironment {
  const value = process.env.EXPO_PUBLIC_APP_ENV
  if (value === 'development' || value === 'preview' || value === 'production') return value
  return process.env.NODE_ENV === 'production' ? 'production' : 'development'
}

export function resolveConfiguredApiUrl(): string {
  // Expo statically inlines direct EXPO_PUBLIC_* references. Do not replace
  // these with dynamic process.env lookups.
  const isReactNative = typeof navigator !== 'undefined' && navigator.product === 'ReactNative'
  if (isReactNative) {
    const environment = expoEnvironment()
    const resolved = resolveApiUrl(process.env.EXPO_PUBLIC_API_URL, {
      environment,
      allowHttp: process.env.EXPO_PUBLIC_ALLOW_HTTP === 'true',
      source: 'expo',
    })

    if (process.env.NODE_ENV !== 'production') {
      const parsed = new URL(resolved)
      console.info('MOBILE_API_CONFIG', {
        environment,
        protocol: parsed.protocol,
        host: parsed.host,
      })
    }
    return resolved
  }

  const environment = process.env.NODE_ENV === 'production' ? 'production' : 'development'
  return resolveNextBrowserApiUrl(
    process.env.NEXT_PUBLIC_API_URL,
    environment,
    typeof window === 'undefined' ? undefined : window.location.origin
  )
}
