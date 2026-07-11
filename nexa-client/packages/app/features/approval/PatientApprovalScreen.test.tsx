import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'
import { PatientApprovalScreen } from './PatientApprovalScreen'
import { getPushRequestStatus, respondToPushRequest } from '../../api/assurance'
import { signConsentChallenge, getDeviceId } from '../../utils/deviceKey'
import { renderWithTamagui } from '../../../../test/test-utils'

vi.mock('../../api/assurance', () => ({
  getPushRequestStatus: vi.fn(),
  respondToPushRequest: vi.fn(),
}))

vi.mock('../../utils/deviceKey', () => ({
  signConsentChallenge: vi.fn(),
  getDeviceId: vi.fn(),
}))

const originalLocation = window.location
beforeEach(() => {
  vi.clearAllMocks()
  // @ts-ignore
  delete window.location
  // @ts-ignore
  window.location = { ...originalLocation, reload: vi.fn() }
})

describe('PatientApprovalScreen', () => {
  const requestId = 'req-123'
  const mockRequest = {
    request_id: requestId,
    patient_id: 'pat-456',
    clinician_id: 'prov-789',
    clinician_name: 'John Smith',
    hospital_name: 'City Hospital',
    purpose: 'Routine Checkup',
    scope: 'patient_summary',
    access_duration: 3600,
    expires_at: new Date(Date.now() + 120000).toISOString(),
    status: 'pending',
    created_at: new Date().toISOString(),
    nonce: 'nonce-789',
  }

  it('renders loading state initially', async () => {
    vi.mocked(getPushRequestStatus).mockResolvedValue(mockRequest as any)
    renderWithTamagui(<PatientApprovalScreen requestId={requestId} />)
    expect(screen.getByText(/Verifying request.../i)).toBeTruthy()
  })

  it('renders pending state with request details', async () => {
    vi.mocked(getPushRequestStatus).mockResolvedValue(mockRequest as any)
    renderWithTamagui(<PatientApprovalScreen requestId={requestId} />)

    await waitFor(() => {
      expect(screen.getByText(/Dr. John Smith/i)).toBeTruthy()
      expect(screen.getByText(/Routine Checkup/i)).toBeTruthy()
      expect(screen.getByText(/Approve/i)).toBeTruthy()
      expect(screen.getByText(/Deny/i)).toBeTruthy()
    })
  })

  it('renders expired state if request has timed out', async () => {
    const expiredRequest = {
      ...mockRequest,
      created_at: new Date(Date.now() - 100000).toISOString(),
    }
    vi.mocked(getPushRequestStatus).mockResolvedValue(expiredRequest as any)
    renderWithTamagui(<PatientApprovalScreen requestId={requestId} />)

    await waitFor(() => {
      expect(screen.getByText(/Request Expired/i)).toBeTruthy()
    })
  })

  it('handles deny flow', async () => {
    vi.mocked(getPushRequestStatus).mockResolvedValue(mockRequest as any)
    vi.mocked(respondToPushRequest).mockResolvedValue({ status: 'ok' })

    renderWithTamagui(<PatientApprovalScreen requestId={requestId} />)

    await waitFor(() => screen.getByText(/Deny/i))
    fireEvent.click(screen.getByRole('button', { name: /Deny/i }))

    await waitFor(() => {
      expect(respondToPushRequest).toHaveBeenCalledWith(requestId, { decision: 'denied' })
      expect(screen.getByRole('heading', { name: /Denied/i })).toBeTruthy()
    })
  })

  it('handles approve flow with 9-pipe signing', async () => {
    vi.mocked(getPushRequestStatus).mockResolvedValue(mockRequest as any)
    vi.mocked(respondToPushRequest).mockResolvedValue({ status: 'ok' })
    vi.mocked(signConsentChallenge).mockResolvedValue('signature-123')
    vi.mocked(getDeviceId).mockResolvedValue('device-abc')

    renderWithTamagui(<PatientApprovalScreen requestId={requestId} />)

    await waitFor(() => screen.getByText(/Approve/i))
    fireEvent.click(screen.getByRole('button', { name: /Approve/i }))

    await waitFor(() => {
      // signConsentChallenge receives the full 9-attribute signing input
      expect(signConsentChallenge).toHaveBeenCalledWith({
        request_id: requestId,
        patient_id: mockRequest.patient_id,
        provider_id: mockRequest.clinician_id,
        nonce: mockRequest.nonce,
        decision: 'approved',
        scope: mockRequest.scope,
        purpose: mockRequest.purpose,
        duration: mockRequest.access_duration,
        expires_at: mockRequest.expires_at,
      })
      expect(respondToPushRequest).toHaveBeenCalledWith(
        requestId,
        expect.objectContaining({
          decision: 'approved',
          signature: 'signature-123',
          nonce: mockRequest.nonce,
        })
      )
      expect(screen.getByText(/Approved/i)).toBeTruthy()
    })
  })

  it('auto-expires when countdown reaches zero', async () => {
    const soonToExpire = {
      ...mockRequest,
      created_at: new Date(Date.now() - 89000).toISOString(),
    }
    vi.mocked(getPushRequestStatus).mockResolvedValue(soonToExpire as any)

    renderWithTamagui(<PatientApprovalScreen requestId={requestId} />)

    await waitFor(() => {
      expect(screen.getByText(/Request Expired/i)).toBeTruthy()
    }, { timeout: 3500 })
  })
})
