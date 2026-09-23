import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ProviderLoginScreen } from './ProviderLoginScreen'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import {
  clearProviderAuthSession,
  getProviderAuthSnapshot,
  storeProviderAuthSession,
} from '../../services/providerAuthSession'

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('expo-router', () => ({
  useRouter: () => ({ push, replace }),
}))
vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 20, bottom: 20, left: 0, right: 0 }),
}))
vi.mock('../../utils/apiClient', () => {
  class MockApiError extends Error {
    constructor(message: string, public status: number, public code?: string) {
      super(message)
    }
  }
  return {
    ApiError: MockApiError,
    NexaApiClient: {
      providerLogin: vi.fn(),
      providerMfaVerify: vi.fn(),
    },
  }
})
vi.mock('../../services/providerAuthSession', () => {
  let token: string | null = null
  let session: any = null
  return {
    storeProviderAuthSession: vi.fn(async (t: string, s: any) => {
      token = t
      session = s
    }),
    clearProviderAuthSession: vi.fn(async () => {
      token = null
      session = null
    }),
    getProviderAuthSnapshot: () => ({
      status: token ? 'authenticated' : 'unauthenticated',
      token,
      session,
      hydrated: true,
    }),
  }
})

describe('ProviderLoginScreen native authentication flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders credentials step with email and password fields without manual UUID input', () => {
    renderWithTamagui(<ProviderLoginScreen />)

    expect(screen.getByLabelText(/Email or Login Identifier/i)).toBeDefined()
    expect(screen.getByLabelText(/Password/i)).toBeDefined()
    expect(screen.getByRole('button', { name: 'Sign In' })).toBeDefined()

    // Must not ask for provider UUID or hospital ID
    expect(screen.queryByLabelText(/Provider ID/i)).toBeNull()
    expect(screen.queryByLabelText(/Hospital ID/i)).toBeNull()
    expect(screen.queryByLabelText(/MFA Token/i)).toBeNull()
  })

  it('handles direct non-MFA login by establishing session and navigating', async () => {
    const futureDate = new Date(Date.now() + 3600 * 1000).toISOString()
    vi.mocked(NexaApiClient.providerLogin).mockResolvedValueOnce({
      access_token: 'direct_bearer_token_abc',
      token_type: 'bearer',
      expires_at: futureDate,
      provider_uid: '11111111-2222-3333-4444-555555555555',
      hospital_id: '99999999-8888-7777-6666-555555555555' as any,
    })

    const onSuccess = vi.fn()
    renderWithTamagui(<ProviderLoginScreen onSuccess={onSuccess} />)

    fireEvent.change(screen.getByLabelText(/Email or Login Identifier/i), {
      target: { value: 'doctor@hospital.in' },
    })
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: 'CorrectPassword123!' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    await waitFor(() => {
      expect(NexaApiClient.providerLogin).toHaveBeenCalledWith({
        login_identifier: 'doctor@hospital.in',
        password: 'CorrectPassword123!',
      })
      expect(storeProviderAuthSession).toHaveBeenCalledWith(
        'direct_bearer_token_abc',
        expect.objectContaining({
          providerUid: '11111111-2222-3333-4444-555555555555',
        })
      )
      expect(onSuccess).toHaveBeenCalled()
    })
  })

  it('transitions to MFA step when mfa_token is returned, keeping challenge internal and showing only TOTP input', async () => {
    const secretMfaToken = 'mfa_internal_challenge_secret_xyz_987'
    vi.mocked(NexaApiClient.providerLogin).mockResolvedValueOnce({
      detail: 'MFA required',
      mfa_token: secretMfaToken,
    })

    renderWithTamagui(<ProviderLoginScreen />)

    fireEvent.change(screen.getByLabelText(/Email or Login Identifier/i), {
      target: { value: 'mfa.doctor@hospital.in' },
    })
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: 'MySecretPassword' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    // Should transition to step 2
    await waitFor(() => {
      expect(screen.getByLabelText(/Authenticator Code/i)).toBeDefined()
      expect(screen.getByRole('button', { name: 'Verify' })).toBeDefined()
    })

    // Raw MFA token MUST NEVER appear in visible UI or DOM text
    expect(screen.queryByText(secretMfaToken)).toBeNull()
    expect(screen.queryByDisplayValue(secretMfaToken)).toBeNull()

    // Step 1 fields are now hidden
    expect(screen.queryByLabelText(/Email or Login Identifier/i)).toBeNull()
  })

  it('submits mfa_token and totp_code upon verification and completes session establishment', async () => {
    const secretMfaToken = 'mfa_internal_challenge_secret_xyz_987'
    vi.mocked(NexaApiClient.providerLogin).mockResolvedValueOnce({
      detail: 'MFA required',
      mfa_token: secretMfaToken,
    })

    const futureDate = new Date(Date.now() + 3600 * 1000).toISOString()
    vi.mocked(NexaApiClient.providerMfaVerify).mockResolvedValueOnce({
      access_token: 'verified_mfa_access_token',
      token_type: 'bearer',
      expires_at: futureDate,
      provider_uid: '11111111-2222-3333-4444-555555555555',
      hospital_id: '99999999-8888-7777-6666-555555555555' as any,
    })

    const onSuccess = vi.fn()
    renderWithTamagui(<ProviderLoginScreen onSuccess={onSuccess} />)

    fireEvent.change(screen.getByLabelText(/Email or Login Identifier/i), {
      target: { value: 'mfa.doctor@hospital.in' },
    })
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: 'MySecretPassword' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    const totpInput = await screen.findByLabelText(/Authenticator Code/i)
    fireEvent.change(totpInput, { target: { value: '123456' } })

    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => {
      expect(NexaApiClient.providerMfaVerify).toHaveBeenCalledWith({
        mfa_token: secretMfaToken,
        totp_code: '123456',
      })
      expect(storeProviderAuthSession).toHaveBeenCalledWith(
        'verified_mfa_access_token',
        expect.objectContaining({
          providerUid: '11111111-2222-3333-4444-555555555555',
        })
      )
      expect(onSuccess).toHaveBeenCalled()
    })
  })

  it('retains recoverable MFA state on invalid code and displays error notice', async () => {
    const secretMfaToken = 'mfa_internal_challenge_secret_xyz_987'
    vi.mocked(NexaApiClient.providerLogin).mockResolvedValueOnce({
      detail: 'MFA required',
      mfa_token: secretMfaToken,
    })

    vi.mocked(NexaApiClient.providerMfaVerify).mockRejectedValueOnce(
      new (ApiError as any)('Invalid authenticator code.', 401)
    )

    renderWithTamagui(<ProviderLoginScreen />)

    fireEvent.change(screen.getByLabelText(/Email or Login Identifier/i), {
      target: { value: 'mfa.doctor@hospital.in' },
    })
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: 'MySecretPassword' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    const totpInput = await screen.findByLabelText(/Authenticator Code/i)
    fireEvent.change(totpInput, { target: { value: '000000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => {
      expect(screen.getByText('Invalid authenticator code. Please try again.')).toBeDefined()
    })

    // Still on MFA screen, user can retry
    expect(screen.getByLabelText(/Authenticator Code/i)).toBeDefined()
  })

  it('allows returning to credentials step via Back to Sign In button', async () => {
    vi.mocked(NexaApiClient.providerLogin).mockResolvedValueOnce({
      detail: 'MFA required',
      mfa_token: 'temp_mfa_token',
    })

    renderWithTamagui(<ProviderLoginScreen />)

    fireEvent.change(screen.getByLabelText(/Email or Login Identifier/i), {
      target: { value: 'mfa.doctor@hospital.in' },
    })
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: 'MySecretPassword' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    await screen.findByLabelText(/Authenticator Code/i)

    fireEvent.click(screen.getByRole('button', { name: 'Back to Sign In' }))

    await waitFor(() => {
      expect(screen.getByLabelText(/Email or Login Identifier/i)).toBeDefined()
    })
  })
})
