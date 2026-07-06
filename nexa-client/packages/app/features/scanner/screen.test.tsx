import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { ScannerScreen } from './screen'
import { getPushRequestStatus, requestPushApproval } from '../../api/assurance'
import { getPatientPolicy } from '../../api/policy'
import { useNfcScanner } from '../../hooks/useNfcScanner'
import { issueRoutineConsentV1 } from '../../api/consent_v1'

vi.mock('../../api/assurance', () => ({
  getPushRequestStatus: vi.fn(),
  requestPushApproval: vi.fn(),
}))

vi.mock('../../api/policy', () => ({
  getPatientPolicy: vi.fn(),
}))

vi.mock('../../hooks/useNfcScanner', () => ({
  useNfcScanner: vi.fn(),
  WEB_MOCK_NFC_CARD_UID: 'mock-uid',
}))

vi.mock('../../api/consent_v1', () => ({
  issueRoutineConsentV1: vi.fn(),
}))

vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

describe('ScannerScreen Doctor Polling', () => {
  const patientId = 'pat-123'
  const requestId = 'req-456'

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useNfcScanner).mockReturnValue({
      status: 'success',
      patientId: patientId,
      canonicalPatientId: null,
      isRedirected: false,
      cardStatus: 'active',
      errorMessage: null,
      isScanning: false,
      startScan: vi.fn(),
      reset: vi.fn(),
    } as any)
  })

  it('starts polling after push request is initiated', async () => {
    vi.mocked(getPatientPolicy).mockResolvedValue({ consent_assurance_policy: 'push_approved' } as any)
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValue({ status: 'pending' } as any)

    render(<ScannerScreen />)

    // Trigger generate consent
    const btn = screen.getByText(/Generate Token & View Record/i)
    act(() => { btn.click() })

    await waitFor(() => {
      expect(requestPushApproval).toHaveBeenCalled()
      expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy()
      expect(screen.getByText(new RegExp(requestId, 'i'))).toBeTruthy()
    })
  })

  it('stops polling and auto-navigates on approval', async () => {
    vi.useFakeTimers()
    vi.mocked(getPatientPolicy).mockResolvedValue({ consent_assurance_policy: 'push_approved' } as any)
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus)
      .mockResolvedValueOnce({ status: 'pending' } as any)
      .mockResolvedValueOnce({ status: 'approved' } as any)
    vi.mocked(issueRoutineConsentV1).mockResolvedValue({ consent_token: 'token-ok' } as any)

    render(<ScannerScreen />)
    
    act(() => { screen.getByText(/Generate Token & View Record/i).click() })

    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    await waitFor(() => {
      expect(screen.getByText(/Request Approved/i)).toBeTruthy()
      expect(issueRoutineConsentV1).toHaveBeenCalledWith(expect.objectContaining({
        consent_assurance: 'push_approved'
      }))
    })
    
    vi.useRealTimers()
  })

  it('handles denial state and stops polling', async () => {
    vi.useFakeTimers()
    vi.mocked(getPatientPolicy).mockResolvedValue({ consent_assurance_policy: 'push_approved' } as any)
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValue({ status: 'denied' } as any)

    render(<ScannerScreen />)
    act(() => { screen.getByText(/Generate Token & View Record/i).click() })

    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    await waitFor(() => {
      expect(screen.getByText(/Request Denied/i)).toBeTruthy()
      expect(screen.getByText(/Try Again/i)).toBeTruthy()
    })
    
    // Status check should only have happened once after denial
    expect(getPushRequestStatus).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('cleans up polling on unmount', async () => {
    vi.useFakeTimers()
    const clearIntervalSpy = vi.spyOn(global, 'clearInterval')
    vi.mocked(getPatientPolicy).mockResolvedValue({ consent_assurance_policy: 'push_approved' } as any)
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValue({ status: 'pending' } as any)

    const { unmount } = render(<ScannerScreen />)
    act(() => { screen.getByText(/Generate Token & View Record/i).click() })
    
    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())

    unmount()
    expect(clearIntervalSpy).toHaveBeenCalled()
    vi.useRealTimers()
  })
})
