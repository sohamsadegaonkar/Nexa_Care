'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, NexaApiClient, setAuthTokenProvider } from '../../utils/apiClient'
import {
  ProviderMfaVerifySuccessSchema,
  SchemaValidationError,
  validateLoginResponse,
  validateOrThrow,
} from '../../schemas/authNfcSchemas'

export const PROVIDER_SESSION_STORAGE_KEY = 'nexa_provider_session_v1'

export interface ProviderIdentity {
  provider_id: string
  display_name: string
  medical_registration_number: string | null
  specialty: string | null
  contact_email: string
  role: string
}

export interface HospitalInfo {
  hospital_id: string
  facility_code: string
  display_name: string
}

export interface ProviderSession {
  access_token: string
  expires_at: string
  provider: ProviderIdentity
  hospital: HospitalInfo
}

export interface ProviderAccessGrant {
  requestId: string
  patientId: string
  consentToken: string
  purpose: string
  scope: 'clinical' | 'full'
  expiresAt: string
}

export type ProviderAuthStatus =
  | 'hydrating'
  | 'unauthenticated'
  | 'mfa_required'
  | 'authenticated'

export type LoginResult = { type: 'authenticated' } | { type: 'mfa_required' }

export interface ProviderAuthState {
  status: ProviderAuthStatus
  hydrated: boolean
  isAuthenticated: boolean
  session: ProviderSession | null
  providerId: string | null
  displayName: string | null
  hospitalName: string | null
  role: string | null
  mfaDetail: string | null
  loginError: string | null
  loggingIn: boolean
  accessGrant: ProviderAccessGrant | null
}

export interface ProviderAuthActions {
  login: (email: string, password: string) => Promise<LoginResult>
  verifyMfa: (totpCode: string) => Promise<void>
  cancelMfa: () => void
  logout: () => void
  setAccessGrant: (grant: ProviderAccessGrant) => void
  clearAccessGrant: () => void
}

export type ProviderAuthContextType = ProviderAuthState & ProviderAuthActions

function buildSession(
  accessToken: string,
  expiresAt: string,
  providerUid: string,
  hospitalId: string,
  email: string,
): ProviderSession {
  return {
    access_token: accessToken,
    expires_at: expiresAt,
    provider: {
      provider_id: providerUid,
      display_name: '',
      medical_registration_number: null,
      specialty: null,
      contact_email: email,
      role: 'clinician',
    },
    hospital: {
      hospital_id: hospitalId,
      facility_code: '',
      display_name: '',
    },
  }
}

function isStoredSession(value: unknown): value is ProviderSession {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<ProviderSession>
  return Boolean(
    typeof record.access_token === 'string' &&
      record.access_token &&
      typeof record.expires_at === 'string' &&
      Date.parse(record.expires_at) > Date.now() &&
      record.provider &&
      typeof record.provider.provider_id === 'string' &&
      record.hospital &&
      typeof record.hospital.hospital_id === 'string',
  )
}

function configurationMessage(error: ApiError): string | null {
  if (error.code === 'MISSING_API_BASE_URL' || error.code === 'INVALID_API_BASE_URL') {
    return 'Provider login is unavailable because NEXT_PUBLIC_API_URL is not configured correctly.'
  }
  if (error.code === 'INSECURE_API_URL') return error.message
  return null
}

function connectionMessage(error: ApiError): string | null {
  if (error.code === 'NETWORK_ERROR') {
    return 'Unable to reach Nexa Care. Check the configured server and your network connection.'
  }
  if (error.code === 'REQUEST_TIMEOUT') {
    return 'The authentication request timed out. Check the server and try again.'
  }
  return null
}

const ProviderAuthContext = createContext<ProviderAuthContextType | null>(null)

export function ProviderAuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ProviderAuthStatus>('hydrating')
  const [session, setSession] = useState<ProviderSession | null>(null)
  const [mfaDetail, setMfaDetail] = useState<string | null>(null)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loggingIn, setLoggingIn] = useState(false)
  const [accessGrant, setAccessGrantState] = useState<ProviderAccessGrant | null>(null)
  const accessTokenRef = useRef<string | null>(null)
  const pendingMfaTokenRef = useRef<string | null>(null)
  const pendingEmailRef = useRef('')
  const operationRef = useRef<Promise<unknown> | null>(null)

  useEffect(() => {
    setAuthTokenProvider(() => accessTokenRef.current)
    try {
      const raw = window.sessionStorage.getItem(PROVIDER_SESSION_STORAGE_KEY)
      const restored: unknown = raw ? JSON.parse(raw) : null
      if (isStoredSession(restored)) {
        accessTokenRef.current = restored.access_token
        setSession(restored)
        setStatus('authenticated')
      } else {
        window.sessionStorage.removeItem(PROVIDER_SESSION_STORAGE_KEY)
        setStatus('unauthenticated')
      }
    } catch {
      window.sessionStorage.removeItem(PROVIDER_SESSION_STORAGE_KEY)
      setStatus('unauthenticated')
    }
  }, [])

  const establishSession = useCallback((next: ProviderSession) => {
    accessTokenRef.current = next.access_token
    setAuthTokenProvider(() => accessTokenRef.current)
    window.sessionStorage.setItem(PROVIDER_SESSION_STORAGE_KEY, JSON.stringify(next))
    pendingMfaTokenRef.current = null
    pendingEmailRef.current = ''
    setMfaDetail(null)
    setSession(next)
    setStatus('authenticated')
    setLoginError(null)
  }, [])

  const login = useCallback((email: string, password: string): Promise<LoginResult> => {
    if (operationRef.current) return operationRef.current as Promise<LoginResult>
    const operation = (async (): Promise<LoginResult> => {
      setLoggingIn(true)
      setLoginError(null)
      pendingMfaTokenRef.current = null
      setMfaDetail(null)
      try {
        const normalizedEmail = email.trim()
        const data = await NexaApiClient.providerLogin({
          login_identifier: normalizedEmail,
          password,
        })
        const validated = validateLoginResponse(data)
        if (validated.type === 'mfa_required') {
          pendingMfaTokenRef.current = validated.data.mfa_token
          pendingEmailRef.current = normalizedEmail
          setMfaDetail(validated.data.detail)
          setStatus('mfa_required')
          return { type: 'mfa_required' }
        }
        establishSession(buildSession(
          validated.data.access_token,
          validated.data.expires_at,
          validated.data.provider_uid,
          String(validated.data.hospital_id),
          normalizedEmail,
        ))
        return { type: 'authenticated' }
      } catch (error) {
        if (error instanceof SchemaValidationError) {
          setLoginError('Server returned an unexpected response. Please contact support.')
        } else if (error instanceof ApiError) {
          setLoginError(
            configurationMessage(error) ??
              connectionMessage(error) ??
              (error.status === 401
                ? 'Invalid email or password.'
                : error.status === 429
                  ? 'Too many attempts. Please wait and try again.'
                  : error.message),
          )
        } else {
          setLoginError('Provider login failed. Please try again.')
        }
        setStatus('unauthenticated')
        throw error
      } finally {
        setLoggingIn(false)
      }
    })().finally(() => {
      operationRef.current = null
    })
    operationRef.current = operation
    return operation
  }, [establishSession])

  const verifyMfa = useCallback((totpCode: string): Promise<void> => {
    if (operationRef.current) return operationRef.current as Promise<void>
    const operation = (async () => {
      const mfaToken = pendingMfaTokenRef.current
      if (!mfaToken) {
        setStatus('unauthenticated')
        setLoginError('MFA session expired. Sign in again.')
        throw new Error('MFA session expired')
      }
      setLoggingIn(true)
      setLoginError(null)
      try {
        const data = await NexaApiClient.providerMfaVerify({
          mfa_token: mfaToken,
          totp_code: totpCode.trim(),
        })
        const validated = validateOrThrow(
          ProviderMfaVerifySuccessSchema,
          data,
          'MFA verify response',
        )
        establishSession(buildSession(
          validated.access_token,
          validated.expires_at,
          validated.provider_uid,
          String(validated.hospital_id),
          pendingEmailRef.current,
        ))
      } catch (error) {
        if (error instanceof ApiError) {
          const expired = error.status === 401 && /session expired/i.test(error.message)
          if (expired) {
            pendingMfaTokenRef.current = null
            pendingEmailRef.current = ''
            setMfaDetail(null)
            setStatus('unauthenticated')
            setLoginError('MFA session expired. Sign in again.')
          } else {
            setLoginError(
              configurationMessage(error) ??
                connectionMessage(error) ??
                (error.status === 401
                  ? 'Invalid authenticator code.'
                  : error.status === 429
                    ? 'Too many attempts. Please wait and try again.'
                    : error.message),
            )
          }
        } else if (error instanceof SchemaValidationError) {
          setLoginError('Server returned an unexpected MFA response. Please contact support.')
        } else {
          setLoginError('MFA verification failed. Please try again.')
        }
        throw error
      } finally {
        setLoggingIn(false)
      }
    })().finally(() => {
      operationRef.current = null
    })
    operationRef.current = operation
    return operation
  }, [establishSession])

  const cancelMfa = useCallback(() => {
    pendingMfaTokenRef.current = null
    pendingEmailRef.current = ''
    setMfaDetail(null)
    setLoginError(null)
    setStatus('unauthenticated')
  }, [])

  const logout = useCallback(() => {
    accessTokenRef.current = null
    pendingMfaTokenRef.current = null
    pendingEmailRef.current = ''
    setAuthTokenProvider(() => null)
    window.sessionStorage.removeItem(PROVIDER_SESSION_STORAGE_KEY)
    setSession(null)
    setAccessGrantState(null)
    setMfaDetail(null)
    setLoginError(null)
    setStatus('unauthenticated')
  }, [])

  const setAccessGrant = useCallback((grant: ProviderAccessGrant) => {
    setAccessGrantState(grant)
  }, [])

  const clearAccessGrant = useCallback(() => {
    setAccessGrantState(null)
  }, [])

  const state: ProviderAuthState = {
    status,
    hydrated: status !== 'hydrating',
    isAuthenticated: status === 'authenticated' && session !== null,
    session,
    providerId: session?.provider.provider_id ?? null,
    displayName: session?.provider.display_name ?? null,
    hospitalName: session?.hospital.display_name ?? null,
    role: session?.provider.role ?? null,
    mfaDetail,
    loginError,
    loggingIn,
    accessGrant,
  }

  return (
    <ProviderAuthContext.Provider
      value={{ ...state, login, verifyMfa, cancelMfa, logout, setAccessGrant, clearAccessGrant }}
    >
      {children}
    </ProviderAuthContext.Provider>
  )
}

export function useProviderAuth(): ProviderAuthContextType {
  const context = useContext(ProviderAuthContext)
  if (!context) throw new Error('useProviderAuth must be used inside ProviderAuthProvider')
  return context
}
