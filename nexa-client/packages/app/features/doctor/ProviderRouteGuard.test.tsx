import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { ProviderAuthProvider } from './ProviderAuthContext'
import { ProviderRouteGuard } from './ProviderRouteGuard'

const replace = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
}))

describe('ProviderRouteGuard', () => {
  beforeEach(() => {
    replace.mockReset()
    window.sessionStorage.clear()
    vi.restoreAllMocks()
    vi.spyOn(NexaApiClient, 'providerWebSession').mockRejectedValue(
      new ApiError('No provider session.', 401, 'HTTP_ERROR')
    )
  })

  it('redirects an unauthenticated consent-history visit with returnTo', async () => {
    renderWithTamagui(
      <ProviderAuthProvider>
        <ProviderRouteGuard returnTo="/consent-history">
          <span>protected content</span>
        </ProviderRouteGuard>
      </ProviderAuthProvider>
    )

    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith('/doctor/login?returnTo=%2Fconsent-history')
    })
    expect(screen.queryByText('protected content')).toBeNull()
  })

  it('renders the protected page after authenticated session hydration', async () => {
    vi.mocked(NexaApiClient.providerWebSession).mockResolvedValue({
      authenticated: true,
      expires_at: '2099-01-01T00:00:00Z',
      provider_uid: 'provider-1',
      hospital_id: 'hospital-1',
      display_name: 'Provider One',
      hospital_name: 'Hospital One',
      roles: ['clinician'],
    })

    renderWithTamagui(
      <ProviderAuthProvider>
        <ProviderRouteGuard returnTo="/consent-history">
          <span>protected content</span>
        </ProviderRouteGuard>
      </ProviderAuthProvider>
    )

    expect(await screen.findByText('protected content')).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })
})
