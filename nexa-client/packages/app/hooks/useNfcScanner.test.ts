import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useNfcScanner } from './useNfcScanner'
import { resolveNfcCard } from '../services/nfcResolve'
vi.mock('../services/nfcResolve', () => ({
  resolveNfcCard: vi.fn(),
  NfcResolveError: class extends Error {},
}))
describe('useNfcScanner', () => {
  beforeEach(() => vi.clearAllMocks())
  it('stores only discovery capability fields', async () => {
    vi.mocked(resolveNfcCard).mockResolvedValue({
      discovery_handle: 'h'.repeat(32),
      expires_at: '2026-08-27T12:00:00Z',
    })
    const { result } = renderHook(() => useNfcScanner())
    await act(async () => {
      await result.current.startScan('CARD-123')
    })
    expect(result.current.discoveryHandle).toBe('h'.repeat(32))
    expect(result.current.expiresAt).toContain('2026-08-27')
    expect(result.current.status).toBe('success')
  })
  it('fails closed without identity fields', async () => {
    vi.mocked(resolveNfcCard).mockRejectedValue(new Error('Scan failed'))
    const { result } = renderHook(() => useNfcScanner())
    await act(async () => {
      await result.current.startScan('CARD-ERR')
    })
    expect(result.current.status).toBe('error')
    expect(result.current.discoveryHandle).toBeNull()
    expect(result.current.errorMessage).toBe('Unable to scan NFC card.')
  })
})
