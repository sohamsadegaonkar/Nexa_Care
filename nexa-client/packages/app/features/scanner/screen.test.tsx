import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, act } from '@testing-library/react'
import { ScannerScreen } from './screen'
import { getPushRequestStatus, requestPushApproval } from '../../api/assurance'
import { getPatientPolicy } from '../../api/policy'
import { useNfcScanner } from '../../hooks/useNfcScanner'
import { issueRoutineConsentV1 } from '../../api/consent_v1'
import { renderWithTamagui } from '../../../../test/test-utils'

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
  ConsentError: class ConsentError extends Error {},
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

    renderWithTamagui(<ScannerScreen />)

    const btn = screen.getByText(/Generate Token & View Record/i)
    act(() => {
      btn.click()
    })

    await waitFor(() => {
      expect(requestPushApproval).toHaveBeenCalled()
      expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy()
      expect(screen.getByText(new RegExp(requestId, 'i'))).toBeTruthy()
    })
  })

  it('stops polling and auto-navigates on approval', async () => {
    vi.mocked(getPatientPolicy).mockResolvedValue({ consent_assurance_policy: 'push_approved' } as any)
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValueOnce({ status: 'pending' } as any).mockResolvedValueOnce({ status: 'approved' } as any)
    vi.mocked(issueRoutineConsentV1).mockResolvedValue({ consent_token: 'token-ok' } as any)

    renderWithTamagui(<ScannerScreen />)

    act(() => {
      screen.getByText(/Generate Token & View Record/i).click()
    })

    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())


    await waitFor(() => {
      expect(screen.getByText(/Request Approved/i)).toBeTruthy()
      expect(issueRoutineConsentV1).toHaveBeenCalledWith(
        expect.objectContaining({
          consent_assurance: 'push_approved',
        })
      )
    }, { timeout: 5500 })

  })

  it('handles denial state and stops polling', async () => {
    vi.mocked(getPatientPolicy).mockResolvedValue({ consent_assurance_policy: 'push_approved' } as any)
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValue({ status: 'denied' } as any)

    renderWithTamagui(<ScannerScreen />)
    act(() => {
      screen.getByText(/Generate Token & View Record/i).click()
    })

    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())


    await waitFor(() => {
      expect(screen.getByText(/Request Denied/i)).toBeTruthy()
      expect(screen.getByText(/Try Again/i)).toBeTruthy()
    }, { timeout: 3500 })

    expect(getPushRequestStatus).toHaveBeenCalledTimes(1)
  })

  it('cleans up polling on unmount', async () => {
    const clearIntervalSpy = vi.spyOn(global, 'clearInterval')
    vi.mocked(getPatientPolicy).mockResolvedValue({ consent_assurance_policy: 'push_approved' } as any)
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValue({ status: 'pending' } as any)

    const { unmount } = renderWithTamagui(<ScannerScreen />)
    act(() => {
      screen.getByText(/Generate Token & View Record/i).click()
    })

    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())

    unmount()
    expect(clearIntervalSpy).toHaveBeenCalled()
  })
})
