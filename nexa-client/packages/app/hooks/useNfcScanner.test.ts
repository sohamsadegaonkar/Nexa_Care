import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useNfcScanner } from './useNfcScanner'
import { resolveNfcCard } from '../api/nfc'

vi.mock('../api/nfc', () => ({
  resolveNfcCard: vi.fn(),
  NfcResolveError: class extends Error {
    constructor(message: string) {
      super(message)
      this.name = 'NfcResolveError'
    }
  },
}))

describe('useNfcScanner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sets success status and patientId on successful scan', async () => {
    vi.mocked(resolveNfcCard).mockResolvedValue({
      patient_id: 'PAT-123',
    } as any)

    const { result } = renderHook(() => useNfcScanner())

    await act(async () => {
      await result.current.startScan('CARD-123')
    })

    expect(result.current.patientId).toBe('PAT-123')
    expect(result.current.status).toBe('success')
    expect(result.current.errorMessage).toBeNull()
    expect(result.current.isScanning).toBe(false)
  })

  it('sets error state when scan fails', async () => {
    vi.mocked(resolveNfcCard).mockRejectedValue(new Error('Scan failed'))

    const { result } = renderHook(() => useNfcScanner())

    await act(async () => {
      await result.current.startScan('CARD-ERR')
    })

    expect(result.current.status).toBe('error')
    expect(result.current.patientId).toBeNull()
    expect(result.current.errorMessage).toBe('Scan failed')
    expect(result.current.isScanning).toBe(false)
  })

  it('reset clears scanner state', async () => {
    vi.mocked(resolveNfcCard).mockResolvedValue({
      patient_id: 'PAT-999',
    } as any)

    const { result } = renderHook(() => useNfcScanner())

    await act(async () => {
      await result.current.startScan('CARD-999')
    })

    expect(result.current.status).toBe('success')
    expect(result.current.patientId).toBe('PAT-999')

    act(() => {
      result.current.reset()
    })

    expect(result.current.status).toBe('idle')
    expect(result.current.patientId).toBeNull()
    expect(result.current.errorMessage).toBeNull()
    expect(result.current.isScanning).toBe(false)
  })
})
