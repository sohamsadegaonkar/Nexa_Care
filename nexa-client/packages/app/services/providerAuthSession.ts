import * as SecureStore from 'expo-secure-store'
import { useSyncExternalStore } from 'react'
import { setAuthTokenProvider } from '../utils/apiClient'

export const PROVIDER_ACCESS_TOKEN_STORAGE_KEY = 'nexa_provider_access_token_v1'
export const PROVIDER_SESSION_INFO_STORAGE_KEY = 'nexa_provider_session_info_v1'

export type ProviderAuthStatus = 'hydrating' | 'unauthenticated' | 'authenticated' | 'expired'

export interface ProviderSessionInfo {
  providerUid: string
  hospitalId: string
  expiresAt: string
}

export interface ProviderAuthSnapshot {
  status: ProviderAuthStatus
  hydrated: boolean
  session: ProviderSessionInfo | null
  token: string | null
}

let accessToken: string | null = null
let sessionInfo: ProviderSessionInfo | null = null
let snapshot: ProviderAuthSnapshot = {
  status: 'hydrating',
  hydrated: false,
  session: null,
  token: null,
}

let hydrationInFlight: Promise<ProviderAuthSnapshot> | null = null
const listeners = new Set<() => void>()

function publish(next: ProviderAuthSnapshot): ProviderAuthSnapshot {
  snapshot = next
  listeners.forEach((listener) => listener())
  return snapshot
}

function isSessionValid(info: ProviderSessionInfo | null): boolean {
  if (!info || !info.expiresAt) return false
  const exp = new Date(info.expiresAt).getTime()
  if (isNaN(exp)) return false
  return exp > Date.now()
}

async function deletePersistedProviderSession(): Promise<void> {
  await Promise.allSettled([
    SecureStore.deleteItemAsync(PROVIDER_ACCESS_TOKEN_STORAGE_KEY),
    SecureStore.deleteItemAsync(PROVIDER_SESSION_INFO_STORAGE_KEY),
  ])
}

export function configureProviderAuthTokenProvider(): void {
  setAuthTokenProvider(() => accessToken)
}

export function getProviderAuthSnapshot(): ProviderAuthSnapshot {
  return snapshot
}

export function subscribeToProviderAuth(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useProviderAuthSession(): ProviderAuthSnapshot {
  return useSyncExternalStore(
    subscribeToProviderAuth,
    getProviderAuthSnapshot,
    getProviderAuthSnapshot
  )
}

export function getCurrentProviderAccessToken(): string | null {
  return snapshot.status === 'authenticated' ? accessToken : null
}

export function getCurrentProviderInfo(): ProviderSessionInfo | null {
  return snapshot.status === 'authenticated' ? sessionInfo : null
}

export function hydrateProviderAuthSession(): Promise<ProviderAuthSnapshot> {
  if (snapshot.hydrated) return Promise.resolve(snapshot)
  if (hydrationInFlight) return hydrationInFlight

  hydrationInFlight = (async () => {
    try {
      const [storedToken, storedInfoJson] = await Promise.all([
        SecureStore.getItemAsync(PROVIDER_ACCESS_TOKEN_STORAGE_KEY),
        SecureStore.getItemAsync(PROVIDER_SESSION_INFO_STORAGE_KEY),
      ])

      if (!storedToken || !storedInfoJson) {
        accessToken = null
        sessionInfo = null
        return publish({
          status: 'unauthenticated',
          hydrated: true,
          session: null,
          token: null,
        })
      }

      let parsedInfo: ProviderSessionInfo | null = null
      try {
        parsedInfo = JSON.parse(storedInfoJson) as ProviderSessionInfo
      } catch {
        parsedInfo = null
      }

      if (!isSessionValid(parsedInfo)) {
        accessToken = null
        sessionInfo = null
        await deletePersistedProviderSession()
        return publish({
          status: 'expired',
          hydrated: true,
          session: null,
          token: null,
        })
      }

      accessToken = storedToken
      sessionInfo = parsedInfo
      configureProviderAuthTokenProvider()
      return publish({
        status: 'authenticated',
        hydrated: true,
        session: parsedInfo,
        token: storedToken,
      })
    } catch {
      accessToken = null
      sessionInfo = null
      return publish({
        status: 'unauthenticated',
        hydrated: true,
        session: null,
        token: null,
      })
    } finally {
      hydrationInFlight = null
    }
  })()

  return hydrationInFlight
}

export async function storeProviderAuthSession(
  token: string,
  info: ProviderSessionInfo
): Promise<void> {
  if (!token || !token.trim()) {
    throw new Error('A valid provider access token is required.')
  }

  try {
    await SecureStore.setItemAsync(PROVIDER_ACCESS_TOKEN_STORAGE_KEY, token.trim(), {
      keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
    })
    await SecureStore.setItemAsync(
      PROVIDER_SESSION_INFO_STORAGE_KEY,
      JSON.stringify(info),
      {
        keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
      }
    )
  } catch (error) {
    await deletePersistedProviderSession()
    throw error
  }

  accessToken = token.trim()
  sessionInfo = info
  configureProviderAuthTokenProvider()
  publish({
    status: 'authenticated',
    hydrated: true,
    session: info,
    token: accessToken,
  })
}

export async function clearProviderAuthSession(): Promise<void> {
  accessToken = null
  sessionInfo = null
  setAuthTokenProvider(() => null)
  await deletePersistedProviderSession()
  publish({
    status: 'unauthenticated',
    hydrated: true,
    session: null,
    token: null,
  })
}
