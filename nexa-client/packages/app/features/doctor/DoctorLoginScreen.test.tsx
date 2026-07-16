import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { DoctorLoginScreen } from './DoctorLoginScreen'
import {
  PROVIDER_SESSION_STORAGE_KEY,
  ProviderAuthProvider,
  useProviderAuth,
} from './ProviderAuthContext'

const replace = vi.fn()
const push = vi.fn()
let returnTo: string | null = null

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push }),
  useSearchParams: () => ({ get: () => returnTo }),
}))

const directLogin = {
  access_token: 'provider-access-token',
  token_type: 'bearer',
  expires_at: '2099-01-01T00:00:00Z',
  provider_uid: 'provider-1',
  hospital_id: 'hospital-1',
}

function renderLogin(showEntryOptions = true) {
  return renderWithTamagui(
    <ProviderAuthProvider>
      <DoctorLoginScreen showEntryOptions={showEntryOptions} />
    </ProviderAuthProvider>,
  )
}

async function submitCredentials() {
  await screen.findByText('Provider Login')
  fireEvent.change(screen.getByPlaceholderText('doctor@hospital.com'), {
    target: { value: 'provider@example.test' },
  })
  fireEvent.change(screen.getByPlaceholderText('Enter password'), {
    target: { value: 'entered-at-runtime' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))
}

describe('provider login state machine', () => {
  beforeEach(() => {
    vi.unstubAllEnvs()
    vi.restoreAllMocks()
    replace.mockReset()
    push.mockReset()
    returnTo = null
    window.sessionStorage.clear()
  })

  it('starts with credentials and never exposes manual MFA fields', async () => {
    renderLogin()

    expect(await screen.findByText('Provider Login')).toBeTruthy()
    expect(screen.getByText('Email or Login Identifier')).toBeTruthy()
    expect(screen.getByText('Password')).toBeTruthy()
    expect(screen.getByPlaceholderText('doctor@hospital.com')).toHaveValue('')
    expect(screen.queryByText('MFA Token')).toBeNull()
    expect(screen.queryByText('Authenticator Code')).toBeNull()
  })

  it('prefills only the identifier when explicit demo mode is enabled', async () => {
    vi.stubEnv('NEXT_PUBLIC_DEMO_MODE', 'true')
    renderLogin()
    expect(await screen.findByText(/Demo mode/)).toBeTruthy()
    expect(screen.getByPlaceholderText('doctor@hospital.com')).toHaveValue('demo.doctor@nexacare.in')
    expect(screen.getByPlaceholderText('Enter password')).toHaveValue('')
  })

  it('establishes a session and opens the dashboard after direct login', async () => {
    const login = vi.spyOn(NexaApiClient, 'providerLogin').mockResolvedValue(directLogin)
    renderLogin()
    await submitCredentials()

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/doctor/dashboard'))
    expect(login).toHaveBeenCalledWith({
      login_identifier: 'provider@example.test',
      password: 'entered-at-runtime',
    })
    expect(window.sessionStorage.getItem(PROVIDER_SESSION_STORAGE_KEY)).not.toContain(
      'entered-at-runtime',
    )
  })

  it('retains the pending token internally and shows only the TOTP field', async () => {
    vi.spyOn(NexaApiClient, 'providerLogin').mockResolvedValue({
      detail: 'Multi-factor authentication required.',
      mfa_token: 'internal-pending-token',
    })
    renderLogin()
    await submitCredentials()

    expect(await screen.findByText('Verify Provider')).toBeTruthy()
    expect(screen.getByText('Authenticator Code')).toBeTruthy()
    expect(screen.queryByText('MFA Token')).toBeNull()
    expect(screen.queryByDisplayValue('internal-pending-token')).toBeNull()
    expect(window.sessionStorage.getItem(PROVIDER_SESSION_STORAGE_KEY)).toBeNull()
  })

  it('verifies MFA with the internal token and establishes the provider session', async () => {
    vi.spyOn(NexaApiClient, 'providerLogin').mockResolvedValue({
      detail: 'Multi-factor authentication required.',
      mfa_token: 'internal-pending-token',
    })
    const verify = vi.spyOn(NexaApiClient, 'providerMfaVerify').mockResolvedValue(directLogin)
    renderLogin()
    await submitCredentials()
    fireEvent.change(await screen.findByPlaceholderText('000000'), {
      target: { value: '123456' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/doctor/dashboard'))
    expect(verify).toHaveBeenCalledWith({
      mfa_token: 'internal-pending-token',
      totp_code: '123456',
    })
  })

  it('maps invalid credentials without exposing backend details', async () => {
    vi.spyOn(NexaApiClient, 'providerLogin').mockRejectedValue(
      new ApiError('backend authentication detail', 401, 'HTTP_ERROR'),
    )
    renderLogin()
    await submitCredentials()

    expect(await screen.findByText('Invalid email or password.')).toBeTruthy()
  })

  it('keeps the MFA step active for an invalid authenticator code', async () => {
    vi.spyOn(NexaApiClient, 'providerLogin').mockResolvedValue({
      detail: 'Multi-factor authentication required.',
      mfa_token: 'internal-pending-token',
    })
    vi.spyOn(NexaApiClient, 'providerMfaVerify').mockRejectedValue(
      new ApiError('Invalid authenticator code.', 401, 'HTTP_ERROR'),
    )
    renderLogin()
    await submitCredentials()
    fireEvent.change(await screen.findByPlaceholderText('000000'), {
      target: { value: '123456' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    expect(await screen.findByText('Invalid authenticator code.')).toBeTruthy()
    expect(screen.getByText('Verify Provider')).toBeTruthy()
  })

  it('returns to provider login when the pending MFA session expires', async () => {
    vi.spyOn(NexaApiClient, 'providerLogin').mockResolvedValue({
      detail: 'Multi-factor authentication required.',
      mfa_token: 'internal-pending-token',
    })
    vi.spyOn(NexaApiClient, 'providerMfaVerify').mockRejectedValue(
      new ApiError('MFA session expired. Sign in again.', 401, 'HTTP_ERROR'),
    )
    renderLogin()
    await submitCredentials()
    fireEvent.change(await screen.findByPlaceholderText('000000'), {
      target: { value: '123456' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    expect(await screen.findByText('Provider Login')).toBeTruthy()
    expect(screen.getByText('MFA session expired. Sign in again.')).toBeTruthy()
  })

  it('prevents duplicate password submissions', async () => {
    let resolveLogin!: (value: typeof directLogin) => void
    const pending = new Promise<typeof directLogin>((resolve) => { resolveLogin = resolve })
    const login = vi.spyOn(NexaApiClient, 'providerLogin').mockReturnValue(pending)
    renderLogin()
    await screen.findByText('Provider Login')
    fireEvent.change(screen.getByPlaceholderText('doctor@hospital.com'), {
      target: { value: 'provider@example.test' },
    })
    fireEvent.change(screen.getByPlaceholderText('Enter password'), {
      target: { value: 'entered-at-runtime' },
    })
    const button = screen.getByRole('button', { name: 'Sign In' })
    fireEvent.click(button)
    fireEvent.click(button)

    expect(login).toHaveBeenCalledOnce()
    resolveLogin(directLogin)
    await waitFor(() => expect(replace).toHaveBeenCalled())
  })

  it('does not log password or tokens', async () => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const normalLog = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    vi.spyOn(NexaApiClient, 'providerLogin').mockResolvedValue(directLogin)
    renderLogin()
    await submitCredentials()
    await waitFor(() => expect(replace).toHaveBeenCalled())

    const output = JSON.stringify([...errorLog.mock.calls, ...normalLog.mock.calls])
    expect(output).not.toContain('entered-at-runtime')
    expect(output).not.toContain('provider-access-token')
  })

  it('preserves the patient and NFC entry routes', async () => {
    renderLogin()
    await screen.findByText('Provider Login')

    fireEvent.click(screen.getByRole('button', { name: 'Continue as Patient' }))
    fireEvent.click(screen.getByRole('button', { name: 'NFC Scanner' }))
    expect(push).toHaveBeenNthCalledWith(1, '/patient/login')
    expect(push).toHaveBeenNthCalledWith(2, '/scanner')
  })

  it('hydrates a valid authenticated session and honors a safe return path', async () => {
    window.sessionStorage.setItem(PROVIDER_SESSION_STORAGE_KEY, JSON.stringify({
      ...directLogin,
      provider: {
        provider_id: 'provider-1', display_name: '', medical_registration_number: null,
        specialty: null, contact_email: 'provider@example.test', role: 'clinician',
      },
      hospital: { hospital_id: 'hospital-1', facility_code: '', display_name: '' },
    }))
    returnTo = '/consent-history'
    renderLogin(false)

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/consent-history'))
  })
})

function AuthenticatedValue() {
  const { hydrated, isAuthenticated } = useProviderAuth()
  return <span>{hydrated ? String(isAuthenticated) : 'hydrating'}</span>
}

describe('provider session hydration', () => {
  it('leaves an unauthenticated refresh at login state', async () => {
    window.sessionStorage.clear()
    renderWithTamagui(
      <ProviderAuthProvider><AuthenticatedValue /></ProviderAuthProvider>,
    )
    expect(await screen.findByText('false')).toBeTruthy()
  })
})
