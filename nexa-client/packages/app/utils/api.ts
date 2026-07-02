import axios, { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios'

const DEFAULT_API_URL = 'https://nexa-care.onrender.com'

export type AuthTokenProvider = () => Promise<string | null | undefined> | string | null | undefined

let authTokenProvider: AuthTokenProvider = () => null

/**
 * Registers the runtime-specific source for the current access token.
 *
 * Next.js and Expo should provide their own secure token lookup instead of
 * storing credentials in this shared package.
 */
export function setAuthTokenProvider(provider: AuthTokenProvider): void {
  authTokenProvider = provider
}

/**
 * Returns the active bearer token, when one is available.
 */
export async function getAuthToken(): Promise<string | null> {
  const token = await authTokenProvider()

  if (typeof token !== 'string') {
    return null
  }

  const trimmedToken = token.trim()

  return trimmedToken.length > 0 ? trimmedToken : null
}

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL,
  headers: {
    Accept: 'application/json',
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use(
  async (config: InternalAxiosRequestConfig): Promise<InternalAxiosRequestConfig> => {
    const token = await getAuthToken()

    if (token) {
      const headers = AxiosHeaders.from(config.headers)
      headers.set('Authorization', `Bearer ${token}`)
      config.headers = headers
    }

    return config
  }
)
