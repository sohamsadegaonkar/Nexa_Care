import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, act } from '@testing-library/react'
import { ScannerScreen } from './screen'
import { claimApprovedAccess, getPushRequestStatus, requestPushApproval } from '../../api/assurance'
import { useNfcScanner } from '../../hooks/useNfcScanner'
import { issueRoutineConsentV1, ConsentError } from '../../api/consent_v1'
import { renderWithTamagui } from '../../../../test/test-utils'

vi.mock('../../api/assurance', () => ({
  claimApprovedAccess: vi.fn(),
  getPushRequestStatus: vi.fn(),
  requestPushApproval: vi.fn(),
}))

vi.mock('../../hooks/useNfcScanner', () => ({
  useNfcScanner: vi.fn(),
  WEB_MOCK_NFC_CARD_UID: 'mock-uid',
}))

vi.mock('../../api/consent_v1', () => ({
  issueRoutineConsentV1: vi.fn(),
  ConsentError: class ConsentError extends Error {
    code: string
    status?: number

    constructor(message: string, code: string, status?: number) {
      super(message)
      this.name = 'ConsentError'
      this.code = code
      this.status = status
    }
  },
}))

const pushMock = vi.fn()

vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push: pushMock }),
}))

const setCapabilityMock = vi.fn()

vi.mock('../../services/capabilityStore', () => ({
  generateWorkflowId: () => 'wf-test-id',
  setCapability: (grant: unknown) => setCapabilityMock(grant),
}))

describe('ScannerScreen Doctor Polling', () => {
  const patientId = 'pat-123'
  const requestId = 'req-456'

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(issueRoutineConsentV1).mockRejectedValue(
      new ConsentError('Patient approval required.', 'CONSENT_UNAUTHORIZED', 428)
    )
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
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValue({ status: 'pending' } as any)

    renderWithTamagui(<ScannerScreen />)

    const btn = screen.getByText(/Generate Token & View Record/i)
    act(() => {
      btn.click()
    })

    await waitFor(() => {
      expect(requestPushApproval).toHaveBeenCalled()
      expect(requestPushApproval).toHaveBeenCalledWith({
        patient_id: patientId,
        purpose: 'ROUTINE_CHECKUP',
        scope: 'clinical',
      })
      expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy()
      expect(screen.getByText(new RegExp(requestId, 'i'))).toBeTruthy()
    })
  })

  it('stops polling and auto-navigates on approval', async () => {
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus)
      .mockResolvedValueOnce({ status: 'pending' } as any)
      .mockResolvedValueOnce({ status: 'approved' } as any)
    vi.mocked(claimApprovedAccess).mockResolvedValue({
      patient_id: patientId,
      consent_token: 'token-ok',
      purpose: 'ROUTINE_CHECKUP',
      scope: 'clinical',
      expires_at: '2026-07-17T12:00:00Z',
    })

    renderWithTamagui(<ScannerScreen />)

    act(() => {
      screen.getByText(/Generate Token & View Record/i).click()
    })

    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())

    await waitFor(
      () => {
        expect(screen.getByText(/Request Approved/i)).toBeTruthy()
        expect(claimApprovedAccess).toHaveBeenCalledWith(requestId)
        expect(setCapabilityMock).toHaveBeenCalledWith(
          expect.objectContaining({ workflowId: 'wf-test-id', patientId, token: 'token-ok' })
        )
        expect(pushMock).toHaveBeenCalledWith(`/patient/${patientId}?workflow_id=wf-test-id`)
        // DEFECT 3: the raw token must never appear in a navigated URL.
        expect(pushMock).not.toHaveBeenCalledWith(expect.stringContaining('token-ok'))
      },
      { timeout: 6500 }
    )
  })

  it('handles denial state and stops polling', async () => {
    vi.mocked(requestPushApproval).mockResolvedValue({ request_id: requestId })
    vi.mocked(getPushRequestStatus).mockResolvedValue({ status: 'denied' } as any)

    renderWithTamagui(<ScannerScreen />)
    act(() => {
      screen.getByText(/Generate Token & View Record/i).click()
    })

    await waitFor(() => expect(screen.getByText(/Waiting for Patient Approval/i)).toBeTruthy())

    await waitFor(
      () => {
        expect(screen.getByText(/Request Denied/i)).toBeTruthy()
        expect(screen.getByText(/Try Again/i)).toBeTruthy()
      },
      { timeout: 3500 }
    )

    expect(getPushRequestStatus).toHaveBeenCalledTimes(1)
  })

  it('cleans up polling on unmount', async () => {
    const clearIntervalSpy = vi.spyOn(global, 'clearInterval')
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
