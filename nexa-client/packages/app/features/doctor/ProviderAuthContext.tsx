'use client'

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError, NexaApiClient, setAuthTokenProvider, setProviderCookieAuthEnabled } from '../../utils/apiClient'

export interface ProviderIdentity {
  provider_id: string
  display_name: string
  medical_registration_number: string | null
  specialty: string | null
  contact_email: string
  role: string
}
export interface HospitalInfo { hospital_id: string; facility_code: string; display_name: string }
export interface ProviderSession { expires_at: string; provider: ProviderIdentity; hospital: HospitalInfo }
export interface ProviderAccessGrant {
  requestId: string; patientId: string; consentToken: string; purpose: string
  scope: 'clinical' | 'full'; expiresAt: string
}
export type ProviderAuthStatus = 'hydrating' | 'unauthenticated' | 'mfa_required' | 'authenticated'
export type LoginResult = { type: 'authenticated' } | { type: 'mfa_required' }
export interface ProviderAuthState {
  status: ProviderAuthStatus; hydrated: boolean; isAuthenticated: boolean; session: ProviderSession | null
  providerId: string | null; displayName: string | null; hospitalName: string | null; role: string | null
  mfaDetail: string | null; loginError: string | null; loggingIn: boolean; accessGrant: ProviderAccessGrant | null
}
export interface ProviderAuthActions {
  login: (email: string, password: string) => Promise<LoginResult>
  verifyMfa: (totpCode: string) => Promise<void>; cancelMfa: () => void; logout: () => void
  setAccessGrant: (grant: ProviderAccessGrant) => void; clearAccessGrant: () => void
}
export type ProviderAuthContextType = ProviderAuthState & ProviderAuthActions

const ProviderAuthContext = createContext<ProviderAuthContextType | null>(null)

function primaryRole(roles: string[]): string {
  return ['admin', 'privacy_officer', 'auditor', 'clinician', 'receptionist'].find((role) => roles.includes(role)) ?? roles[0] ?? 'none'
}

export function ProviderAuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ProviderAuthStatus>('hydrating')
  const [session, setSession] = useState<ProviderSession | null>(null)
  const [mfaDetail, setMfaDetail] = useState<string | null>(null)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loggingIn, setLoggingIn] = useState(false)
  const [accessGrant, setAccessGrantState] = useState<ProviderAccessGrant | null>(null)
  const operationRef = useRef<Promise<unknown> | null>(null)

  const hydrate = useCallback(async (): Promise<boolean> => {
    try {
      const data = await NexaApiClient.providerWebSession()
      const next: ProviderSession = {
        expires_at: data.expires_at,
        provider: {
          provider_id: data.provider_uid, display_name: data.display_name,
          medical_registration_number: null, specialty: null, contact_email: '', role: primaryRole(data.roles),
        },
        hospital: { hospital_id: String(data.hospital_id), facility_code: '', display_name: data.hospital_name },
      }
      setSession(next); setStatus('authenticated'); setLoginError(null)
      return true
    } catch {
      setSession(null); setStatus('unauthenticated')
      return false
    }
  }, [])

  useEffect(() => {
    // Browser provider auth is cookie-only. Native patient auth continues to
    // supply its SecureStore bearer token through the shared client.
    setAuthTokenProvider(() => null)
    setProviderCookieAuthEnabled(true)
    void hydrate()
    return () => setProviderCookieAuthEnabled(false)
  }, [hydrate])

  const login = useCallback((email: string, password: string): Promise<LoginResult> => {
    if (operationRef.current) return operationRef.current as Promise<LoginResult>
    const operation = (async () => {
      setLoggingIn(true); setLoginError(null)
      try {
        const result = await NexaApiClient.providerWebLogin({ login_identifier: email.trim(), password })
        if (result.status === 'mfa_required') {
          setMfaDetail('Multi-factor authentication required.'); setStatus('mfa_required')
          return { type: 'mfa_required' } as const
        }
        if (!await hydrate()) throw new Error('Session establishment failed')
        return { type: 'authenticated' } as const
      } catch (error) {
        setStatus('unauthenticated')
        setLoginError(error instanceof ApiError && error.status === 401 ? 'Invalid email or password.' : 'Provider login failed. Please try again.')
        throw error
      } finally { setLoggingIn(false) }
    })().finally(() => { operationRef.current = null })
    operationRef.current = operation
    return operation
  }, [hydrate])

  const verifyMfa = useCallback(async (totpCode: string) => {
    setLoggingIn(true); setLoginError(null)
    try {
      await NexaApiClient.providerWebMfaVerify(totpCode.trim())
      if (!await hydrate()) throw new Error('Session establishment failed')
      setMfaDetail(null)
    } catch (error) {
      setLoginError(error instanceof ApiError && error.status === 401 ? 'Invalid authenticator code.' : 'MFA verification failed. Please try again.')
      throw error
    } finally { setLoggingIn(false) }
  }, [hydrate])

  const cancelMfa = useCallback(() => { setMfaDetail(null); setLoginError(null); setStatus('unauthenticated') }, [])
  const logout = useCallback(() => {
    void NexaApiClient.providerWebLogout().finally(() => {
      setSession(null); setAccessGrantState(null); setMfaDetail(null); setLoginError(null); setStatus('unauthenticated')
    })
  }, [])
  const setAccessGrant = useCallback((grant: ProviderAccessGrant) => setAccessGrantState(grant), [])
  const clearAccessGrant = useCallback(() => setAccessGrantState(null), [])
  const state: ProviderAuthState = {
    status, hydrated: status !== 'hydrating', isAuthenticated: status === 'authenticated' && session !== null, session,
    providerId: session?.provider.provider_id ?? null, displayName: session?.provider.display_name ?? null,
    hospitalName: session?.hospital.display_name ?? null, role: session?.provider.role ?? null,
    mfaDetail, loginError, loggingIn, accessGrant,
  }
  return <ProviderAuthContext.Provider value={{ ...state, login, verifyMfa, cancelMfa, logout, setAccessGrant, clearAccessGrant }}>{children}</ProviderAuthContext.Provider>
}

export function useProviderAuth(): ProviderAuthContextType {
  const context = useContext(ProviderAuthContext)
  if (!context) throw new Error('useProviderAuth must be used inside ProviderAuthProvider')
  return context
}
