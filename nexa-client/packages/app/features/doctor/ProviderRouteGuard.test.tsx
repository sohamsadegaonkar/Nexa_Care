import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import {
  PROVIDER_SESSION_STORAGE_KEY,
  ProviderAuthProvider,
} from './ProviderAuthContext'
import { ProviderRouteGuard } from './ProviderRouteGuard'

const replace = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
}))

describe('ProviderRouteGuard', () => {
  beforeEach(() => {
    replace.mockReset()
    window.sessionStorage.clear()
  })

  it('redirects an unauthenticated consent-history visit with returnTo', async () => {
    renderWithTamagui(
      <ProviderAuthProvider>
        <ProviderRouteGuard returnTo="/consent-history">
          <span>protected content</span>
        </ProviderRouteGuard>
      </ProviderAuthProvider>,
    )

    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith(
        '/doctor/login?returnTo=%2Fconsent-history',
      )
    })
    expect(screen.queryByText('protected content')).toBeNull()
  })

  it('renders the protected page after authenticated session hydration', async () => {
    window.sessionStorage.setItem(PROVIDER_SESSION_STORAGE_KEY, JSON.stringify({
      access_token: 'provider-access-token',
      expires_at: '2099-01-01T00:00:00Z',
      provider: {
        provider_id: 'provider-1', display_name: '', medical_registration_number: null,
        specialty: null, contact_email: 'provider@example.test', role: 'clinician',
      },
      hospital: { hospital_id: 'hospital-1', facility_code: '', display_name: '' },
    }))

    renderWithTamagui(
      <ProviderAuthProvider>
        <ProviderRouteGuard returnTo="/consent-history">
          <span>protected content</span>
        </ProviderRouteGuard>
      </ProviderAuthProvider>,
    )

    expect(await screen.findByText('protected content')).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })
})
