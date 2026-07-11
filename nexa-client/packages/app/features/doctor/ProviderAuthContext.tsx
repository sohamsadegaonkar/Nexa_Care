/**
 * Provider session context for the doctor web app.
 *
 * Holds the logged-in provider's session token and provider info.
 * ALL provider_id values come from this context — NEVER hardcoded.
 *
 * Login flow:
 *   1. POST /api/v2/auth/login → { access_token, provider_uid, hospital_id }
 *      OR { detail, mfa_token } if MFA is required
 *   2. If MFA required: POST /api/v2/auth/mfa/verify → { access_token, ... }
 *
 * RUNTIME VALIDATION: All backend responses are validated against Zod schemas
 * BEFORE the application trusts them. If the backend contract changes, the
 * frontend will fail with a clear SchemaValidationError rather than silently
 * corrupting state with unexpected data shapes.
 *
 * ALPHA: Token stored in memory only (not SecureStore/httpOnly cookie).
 * Not yet: automatic token refresh with queue, certificate pinning.
 *
 * Security notes:
 *   - Role on the dashboard comes from the session built from the backend
 *     response, NOT from client-controlled state. However, the backend does
 *     not currently return a role field in the login response — it is
 *     defaulted to 'clinician'. This MUST be replaced with a backend-issued
 *     role from a signed token before production.
 *   - MFA challenges are server-side Redis tokens (single-use, TTL-bounded).
 *     The frontend never generates or stores MFA challenge state.
 *   - Tokens do NOT survive page reload (in-memory only). This is an ALPHA
 *     limitation — production must use SecureStore or httpOnly cookies.
 *   - Logout clears the in-memory JWT and session state. It does NOT call
 *     POST /api/v2/auth/logout (server-side token invalidation) yet.
 *     Production MUST add server-side invalidation.
 */

'use client'

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'
import { NexaApiClient, ApiError } from '../../utils/apiClient'
import { setAuthTokenProvider } from '../../utils/apiClient'
import {
  validateLoginResponse,
  validateOrThrow,
  ProviderMfaVerifySuccessSchema,
  SchemaValidationError,
} from '../../schemas/authNfcSchemas'

// ── Public types ─────────────────────────────────────────────────────────────

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
  provider: ProviderIdentity
  hospital: HospitalInfo
}

/** Result of calling login() — caller must check type. */
export type LoginResult =
  | { type: 'authenticated'; session: ProviderSession }
  | { type: 'mfa_required'; mfaToken: string; detail: string }

export interface ProviderAuthState {
  isAuthenticated: boolean
  session: ProviderSession | null
  providerId: string | null
  displayName: string | null
  hospitalName: string | null
  role: string | null
  loginError: string | null
  loggingIn: boolean
}

export interface ProviderAuthActions {
  /** Start login. Returns LoginResult — either authenticated or mfa_required. */
  login: (email: string, password: string) => Promise<LoginResult>
  /** Complete MFA verification. Stores session on success. */
  verifyMfa: (mfaToken: string, totpCode: string) => Promise<void>
  /** Log out. Clears session and JWT. */
  logout: () => void
}

export type ProviderAuthContextType = ProviderAuthState & ProviderAuthActions

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildSession(
  accessToken: string,
  providerUid: string,
  hospitalId: string,
  email: string,
): ProviderSession {
  return {
    access_token: accessToken,
    provider: {
      provider_id: providerUid,
      display_name: '',
      medical_registration_number: null,
      specialty: null,
      contact_email: email,
      // ALPHA: Role is defaulted to 'clinician' because the backend login
      // response does not yet include a role field. The role shown on the
      // dashboard is NOT from a signed backend claim yet. Production MUST
      // extract role from the verified JWT payload, not default it here.
      role: 'clinician',
    },
    hospital: {
      hospital_id: hospitalId,
      facility_code: '',
      display_name: '',
    },
  }
}

// ── Context ──────────────────────────────────────────────────────────────────

const ProviderAuthContext = createContext<ProviderAuthContextType | null>(null)

// ── Provider ─────────────────────────────────────────────────────────────────

export function ProviderAuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<ProviderSession | null>(null)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loggingIn, setLoggingIn] = useState(false)

  const login = useCallback(async (email: string, password: string): Promise<LoginResult> => {
    setLoggingIn(true)
    setLoginError(null)
    try {
      const data = await NexaApiClient.login({
        login_identifier: email.trim(),
        password,
      })

      // Validate backend response at runtime before trusting it
      const validated = validateLoginResponse(data)

      if (validated.type === 'mfa_required') {
        return { type: 'mfa_required', mfaToken: validated.data.mfa_token, detail: validated.data.detail }
      }

      // Direct login success (no MFA) — data is now Zod-validated
      const accessToken = validated.data.access_token
      const tokenRef = { current: accessToken }
      setAuthTokenProvider(() => tokenRef.current)

      const newSession = buildSession(
        accessToken,
        validated.data.provider_uid,
        String(validated.data.hospital_id),
        email.trim(),
      )
      setSession(newSession)
      return { type: 'authenticated', session: newSession }
    } catch (err) {
      if (err instanceof SchemaValidationError) {
        setLoginError(
          'Server returned an unexpected response. Please try again or contact support.',
        )
      } else {
        const message =
          err instanceof ApiError
            ? err.status === 401
              ? 'Invalid email or password.'
              : err.status === 429
                ? 'Too many attempts. Please wait and try again.'
                : err.message
            : err instanceof Error
              ? err.message
              : 'Login failed. Please try again.'
        setLoginError(message)
      }
      throw err
    } finally {
      setLoggingIn(false)
    }
  }, [])

  const verifyMfa = useCallback(async (mfaToken: string, totpCode: string): Promise<void> => {
    setLoggingIn(true)
    setLoginError(null)
    try {
      const data = await NexaApiClient.verifyMfa({
        mfa_token: mfaToken,
        totp_code: totpCode.trim(),
      })

      // Validate MFA verify response at runtime
      const validated = validateOrThrow(ProviderMfaVerifySuccessSchema, data, 'MFA verify response')

      const accessToken = validated.access_token
      const tokenRef = { current: accessToken }
      setAuthTokenProvider(() => tokenRef.current)

      const newSession = buildSession(
        accessToken,
        validated.provider_uid,
        String(validated.hospital_id),
        '',
      )
      setSession(newSession)
    } catch (err) {
      if (err instanceof SchemaValidationError) {
        setLoginError(
          'Server returned an unexpected MFA response. Please try again or contact support.',
        )
      } else {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'MFA verification failed. Please try again.'
        setLoginError(message)
      }
      throw err
    } finally {
      setLoggingIn(false)
    }
  }, [])

  const logout = useCallback(() => {
    setAuthTokenProvider(() => null)
    setSession(null)
    setLoginError(null)
    // ALPHA: Does not call POST /api/v2/auth/logout for server-side
    // token invalidation. Production MUST add this call.
  }, [])

  const state: ProviderAuthState = {
    isAuthenticated: session !== null,
    session,
    providerId: session?.provider?.provider_id ?? null,
    displayName: session?.provider?.display_name ?? null,
    hospitalName: session?.hospital?.display_name ?? null,
    role: session?.provider?.role ?? null,
    loginError,
    loggingIn,
  }

  const actions: ProviderAuthActions = { login, verifyMfa, logout }

  return (
    <ProviderAuthContext.Provider value={{ ...state, ...actions }}>
      {children}
    </ProviderAuthContext.Provider>
  )
}

// ── Hook ─────────────────────────────────────────────────────────────────────

/**
 * Access the provider auth context.
 *
 * MUST be used inside a <ProviderAuthProvider>.
 * Returns the full auth state + login/verifyMfa/logout actions.
 *
 * Use `providerId` from this hook — NEVER hardcode a provider_id.
 */
export function useProviderAuth(): ProviderAuthContextType {
  const ctx = useContext(ProviderAuthContext)
  if (!ctx) {
    throw new Error(
      'useProviderAuth must be used inside a <ProviderAuthProvider>',
    )
  }
  return ctx
}
