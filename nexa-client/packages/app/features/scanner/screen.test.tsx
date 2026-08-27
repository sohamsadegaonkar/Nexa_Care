import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, fireEvent } from '@testing-library/react'
import { ScannerScreen } from './screen'
import { useNfcScanner } from '../../hooks/useNfcScanner'
import { renderWithTamagui } from '../../../../test/test-utils'

const push = vi.fn()
const setDiscoverySelection = vi.fn()
vi.mock('../../hooks/useNfcScanner', () => ({
  useNfcScanner: vi.fn(),
  WEB_MOCK_NFC_CARD_UID: 'mock-uid',
}))
vi.mock('../doctor/ProviderAuthContext', () => ({
  useProviderAuth: () => ({ isAuthenticated: true, setDiscoverySelection }),
}))
vi.mock('solito/navigation', () => ({ useRouter: () => ({ push }) }))

describe('ScannerScreen secure discovery flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useNfcScanner).mockReturnValue({
      status: 'idle',
      discoveryHandle: null,
      expiresAt: null,
      errorMessage: null,
      isScanning: false,
      startScan: vi.fn(),
      reset: vi.fn(),
    })
  })
  it('stores only the secure NFC result and opens canonical consent request', async () => {
    const startScan = vi
      .fn()
      .mockResolvedValue({ discovery_handle: 'h'.repeat(32), expires_at: '2026-08-27T12:00:00Z' })
    vi.mocked(useNfcScanner).mockReturnValue({
      status: 'idle',
      discoveryHandle: null,
      expiresAt: null,
      errorMessage: null,
      isScanning: false,
      startScan,
      reset: vi.fn(),
    })
    renderWithTamagui(<ScannerScreen />)
    fireEvent.click(screen.getByRole('button', { name: /simulate nfc tap/i }))
    await waitFor(() => expect(push).toHaveBeenCalledWith('/doctor/request-consent'))
    expect(startScan).toHaveBeenCalledWith('mock-uid')
    expect(setDiscoverySelection).toHaveBeenCalledWith(
      expect.objectContaining({ discoveryHandle: 'h'.repeat(32), source: 'nfc' })
    )
    expect(screen.queryByText(/patient id:/i)).toBeNull()
  })
  it('does not expose identity or navigate on an NFC failure', () => {
    vi.mocked(useNfcScanner).mockReturnValue({
      status: 'error',
      discoveryHandle: null,
      expiresAt: null,
      errorMessage: 'NFC card could not be resolved.',
      isScanning: false,
      startScan: vi.fn(),
      reset: vi.fn(),
    })
    renderWithTamagui(<ScannerScreen />)
    expect(screen.getByText('NFC card could not be resolved.')).toBeTruthy()
    expect(push).not.toHaveBeenCalled()
    expect(setDiscoverySelection).not.toHaveBeenCalled()
  })
})
