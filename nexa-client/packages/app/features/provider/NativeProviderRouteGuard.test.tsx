import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NativeProviderRouteGuard } from './NativeProviderRouteGuard'

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))
vi.mock('expo-router', () => ({
  useRouter: () => ({ replace }),
}))

let mockStatus = 'unauthenticated'
let mockHydrated = true
vi.mock('../../services/providerAuthSession', () => ({
  useProviderAuthSession: () => ({
    status: mockStatus,
    hydrated: mockHydrated,
    session: mockStatus === 'authenticated' ? { providerUid: '123' } : null,
    token: mockStatus === 'authenticated' ? 'tok' : null,
  }),
}))

describe('NativeProviderRouteGuard', () => {
  it('redirects to /provider/login when provider session is unauthenticated', () => {
    mockStatus = 'unauthenticated'
    mockHydrated = true
    replace.mockClear()

    renderWithTamagui(
      <NativeProviderRouteGuard>
        <div>Secret Clinical Records</div>
      </NativeProviderRouteGuard>
    )

    expect(replace).toHaveBeenCalledWith('/provider/login')
    expect(screen.queryByText('Secret Clinical Records')).toBeNull()
  })

  it('renders loading state while provider session is hydrating', () => {
    mockStatus = 'hydrating'
    mockHydrated = false
    replace.mockClear()

    renderWithTamagui(
      <NativeProviderRouteGuard>
        <div>Secret Clinical Records</div>
      </NativeProviderRouteGuard>
    )

    expect(screen.getByText('Verifying provider session...')).toBeDefined()
    expect(screen.queryByText('Secret Clinical Records')).toBeNull()
  })

  it('renders protected child content when provider is authenticated', () => {
    mockStatus = 'authenticated'
    mockHydrated = true
    replace.mockClear()

    renderWithTamagui(
      <NativeProviderRouteGuard>
        <div>Secret Clinical Records</div>
      </NativeProviderRouteGuard>
    )

    expect(screen.getByText('Secret Clinical Records')).toBeDefined()
    expect(replace).not.toHaveBeenCalled()
  })
})
