import * as SecureStore from 'expo-secure-store'
import { useSyncExternalStore } from 'react'
import { setAuthTokenProvider } from '../utils/apiClient'

export const PATIENT_ACCESS_TOKEN_STORAGE_KEY = 'nexa_patient_access_token_v1'
export const DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY = 'nexa_device_enrollment_token_v1'

export type PatientAuthStatus = 'hydrating' | 'unauthenticated' | 'authenticated' | 'expired'

export interface PatientAuthSnapshot {
  status: PatientAuthStatus
  hydrated: boolean
  sessionKey: string | null
}

interface PatientJwtClaims {
  sub: string
  patient_id: string
  exp: number
  jti?: string
}

let accessToken: string | null = null
let snapshot: PatientAuthSnapshot = {
  status: 'hydrating',
  hydrated: false,
  sessionKey: null,
}
let hydrationInFlight: Promise<PatientAuthSnapshot> | null = null
const listeners = new Set<() => void>()

function publish(next: PatientAuthSnapshot): PatientAuthSnapshot {
  snapshot = next
  for (const listener of listeners) listener()
  return snapshot
}

function decodeBase64Url(value: string): string {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
  if (typeof atob !== 'function') throw new Error('Base64 decoding is unavailable')
  return atob(padded)
}

function readPatientClaims(token: string): PatientJwtClaims | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3 || !parts[1]) return null
    const value = JSON.parse(decodeBase64Url(parts[1])) as Partial<PatientJwtClaims>
    if (
      typeof value.sub !== 'string' ||
      typeof value.patient_id !== 'string' ||
      value.sub !== value.patient_id ||
      typeof value.exp !== 'number'
    )
      return null
    return value as PatientJwtClaims
  } catch {
    return null
  }
}

function authenticatedSnapshot(token: string): PatientAuthSnapshot | null {
  const claims = readPatientClaims(token)
  if (!claims || claims.exp * 1000 <= Date.now()) return null
  return {
    status: 'authenticated',
    hydrated: true,
    sessionKey: `${claims.patient_id}:${claims.jti ?? claims.exp}`,
  }
}

async function deletePersistedSession(): Promise<void> {
  await Promise.allSettled([
    SecureStore.deleteItemAsync(PATIENT_ACCESS_TOKEN_STORAGE_KEY),
    SecureStore.deleteItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY),
  ])
}

export function configurePatientAuthTokenProvider(): void {
  setAuthTokenProvider(() => accessToken)
}

export function getPatientAuthSnapshot(): PatientAuthSnapshot {
  return snapshot
}

export function subscribeToPatientAuth(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function usePatientAuthSession(): PatientAuthSnapshot {
  return useSyncExternalStore(
    subscribeToPatientAuth,
    getPatientAuthSnapshot,
    getPatientAuthSnapshot
  )
}

export function getCurrentPatientAccessToken(): string | null {
  return snapshot.status === 'authenticated' ? accessToken : null
}

export function hydratePatientAuthSession(): Promise<PatientAuthSnapshot> {
  if (snapshot.hydrated) return Promise.resolve(snapshot)
  if (hydrationInFlight) return hydrationInFlight

  hydrationInFlight = SecureStore.getItemAsync(PATIENT_ACCESS_TOKEN_STORAGE_KEY)
    .then(async (storedToken) => {
      if (!storedToken) {
        accessToken = null
        return publish({ status: 'unauthenticated', hydrated: true, sessionKey: null })
      }
      const next = authenticatedSnapshot(storedToken)
      if (!next) {
        accessToken = null
        await deletePersistedSession()
        return publish({ status: 'expired', hydrated: true, sessionKey: null })
      }
      accessToken = storedToken
      return publish(next)
    })
    .catch(() => {
      accessToken = null
      return publish({ status: 'unauthenticated', hydrated: true, sessionKey: null })
    })
    .finally(() => {
      hydrationInFlight = null
    })
  return hydrationInFlight
}

export async function storePatientAuthSession(
  token: string,
  enrollmentToken: string
): Promise<void> {
  const next = authenticatedSnapshot(token)
  if (!next) throw new Error('The patient session returned by the server is invalid or expired.')
  try {
    await Promise.all([
      SecureStore.setItemAsync(PATIENT_ACCESS_TOKEN_STORAGE_KEY, token, {
        keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
      }),
      SecureStore.setItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY, enrollmentToken, {
        keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
      }),
    ])
  } catch (error) {
    await deletePersistedSession()
    throw error
  }
  accessToken = token
  configurePatientAuthTokenProvider()
  publish(next)
}

export async function clearPatientAuthSession(
  reason: 'logout' | 'expired' = 'logout'
): Promise<void> {
  accessToken = null
  await deletePersistedSession()
  publish({
    status: reason === 'expired' ? 'expired' : 'unauthenticated',
    hydrated: true,
    sessionKey: null,
  })
}
