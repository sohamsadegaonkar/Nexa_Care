import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useNfcScanner } from './useNfcScanner'
import { resolveNfcCard } from '../api/nfc'

vi.mock('../api/nfc', () => ({
  resolveNfcCard: vi.fn(),
  NfcResolveError: class extends Error {
    constructor(message: string) {
      super(message)
    }
  },
}))

describe('useNfcScanner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('handles non-merged scan correctly', async () => {
    const mockResponse = {
      patient_id: 'PAT-123',
      canonical_patient_id: null,
      is_redirected: false,
      card_status: 'active' as const,
    }
    vi.mocked(resolveNfcCard).mockResolvedValue(mockResponse)

    const { result } = renderHook(() => useNfcScanner())

    await act(async () => {
      await result.current.startScan('CARD-123')
    })

    expect(result.current.patientId).toBe('PAT-123')
    expect(result.current.canonicalPatientId).toBeNull()
    expect(result.current.isRedirected).toBe(false)
    expect(result.current.cardStatus).toBe('active')
    expect(result.current.status).toBe('success')
  })

  it('handles merged scan with redirect correctly', async () => {
    const mockResponse = {
      patient_id: 'PAT-OLD',
      canonical_patient_id: 'PAT-NEW',
      is_redirected: true,
      card_status: 'active' as const,
    }
    vi.mocked(resolveNfcCard).mockResolvedValue(mockResponse)

    const { result } = renderHook(() => useNfcScanner())

    await act(async () => {
      await result.current.startScan('CARD-OLD')
    })

    expect(result.current.patientId).toBe('PAT-OLD')
    expect(result.current.canonicalPatientId).toBe('PAT-NEW')
    expect(result.current.isRedirected).toBe(true)
    expect(result.current.cardStatus).toBe('active')
    expect(result.current.status).toBe('success')
  })

  it('handles inactive card scan correctly', async () => {
    const mockResponse = {
      patient_id: 'PAT-123',
      canonical_patient_id: null,
      is_redirected: false,
      card_status: 'inactive' as const,
    }
    vi.mocked(resolveNfcCard).mockResolvedValue(mockResponse)

    const { result } = renderHook(() => useNfcScanner())

    await act(async () => {
      await result.current.startScan('CARD-INACTIVE')
    })

    expect(result.current.patientId).toBe('PAT-123')
    expect(result.current.cardStatus).toBe('inactive')
    expect(result.current.status).toBe('success')
  })
})
