import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'
import { renderWithTamagui } from '../../../../test/test-utils'
import { HomeScreen } from './screen'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push }),
}))
vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 20, bottom: 20, left: 0, right: 0 }),
}))

describe('Mobile public entry HomeScreen', () => {
  it('presents clean role-selection options for Patient and Healthcare Provider', () => {
    renderWithTamagui(<HomeScreen />)

    expect(screen.getByText('Continue as Patient')).toBeDefined()
    expect(screen.getByText('Continue as Healthcare Provider')).toBeDefined()
    expect(screen.getAllByText('Nexa Care').length).toBeGreaterThanOrEqual(1)
  })

  it('does NOT expose internal debug forms, tokens, or privileged operational controls', () => {
    renderWithTamagui(<HomeScreen />)

    // Form inputs must not exist on root
    expect(screen.queryByPlaceholderText(/mfa token/i)).toBeNull()
    expect(screen.queryByPlaceholderText(/provider id/i)).toBeNull()
    expect(screen.queryByPlaceholderText(/authenticator code/i)).toBeNull()
    expect(screen.queryByText(/provider verification/i)).toBeNull()

    // Privileged operational shortcuts must not exist on root
    expect(screen.queryByText(/nfc scanner/i)).toBeNull()
    expect(screen.queryByText(/emergency break-glass/i)).toBeNull()
    expect(screen.queryByText(/dashboard/i)).toBeNull()
    expect(screen.queryByText(/consent history/i)).toBeNull()
  })

  it('navigates to patient login when Continue as Patient is pressed', () => {
    const onNavigate = vi.fn()
    renderWithTamagui(<HomeScreen onNavigate={onNavigate} />)

    fireEvent.click(screen.getByText('Continue as Patient'))
    expect(onNavigate).toHaveBeenCalledWith('patient/login')
  })

  it('navigates to provider login when Continue as Healthcare Provider is pressed', () => {
    const onNavigate = vi.fn()
    renderWithTamagui(<HomeScreen onNavigate={onNavigate} />)

    fireEvent.click(screen.getByText('Continue as Healthcare Provider'))
    expect(onNavigate).toHaveBeenCalledWith('provider/login')
  })

  it('falls back to solito router when onNavigate is omitted', () => {
    push.mockClear()
    renderWithTamagui(<HomeScreen />)

    fireEvent.click(screen.getByText('Continue as Patient'))
    expect(push).toHaveBeenCalledWith('/patient/login')

    fireEvent.click(screen.getByText('Continue as Healthcare Provider'))
    expect(push).toHaveBeenCalledWith('/provider/login')
  })
})
