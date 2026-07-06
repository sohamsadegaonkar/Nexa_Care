import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { PatientApprovalScreen } from './PatientApprovalScreen'
import { getPushRequestStatus, respondToPushRequest } from '../../api/assurance'
import * as LocalAuthentication from 'expo-local-authentication'

vi.mock('../../api/assurance', () => ({
  getPushRequestStatus: vi.fn(),
  respondToPushRequest: vi.fn(),
}))

vi.mock('expo-local-authentication', () => ({
  hasHardwareAsync: vi.fn(),
  isEnrolledAsync: vi.fn(),
  authenticateAsync: vi.fn(),
}))

// Mock window.location.reload
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
    clinician_name: 'John Smith',
    hospital_name: 'City Hospital',
    purpose: 'Routine Checkup',
    status: 'pending',
    created_at: new Date().toISOString(),
    nonce: 'nonce-789'
  }

  it('renders loading state initially', async () => {
    vi.mocked(getPushRequestStatus).mockReturnValue(new Promise(() => {}))
    render(<PatientApprovalScreen requestId={requestId} />)
    expect(screen.getByText(/Verifying request.../i)).toBeTruthy()
  })

  it('renders pending state with request details', async () => {
    vi.mocked(getPushRequestStatus).mockResolvedValue(mockRequest as any)
    render(<PatientApprovalScreen requestId={requestId} />)
    
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
      created_at: new Date(Date.now() - 100000).toISOString() // > 90s ago
    }
    vi.mocked(getPushRequestStatus).mockResolvedValue(expiredRequest as any)
    render(<PatientApprovalScreen requestId={requestId} />)
    
    await waitFor(() => {
      expect(screen.getByText(/Request Expired/i)).toBeTruthy()
    })
  })

  it('handles deny flow', async () => {
    vi.mocked(getPushRequestStatus).mockResolvedValue(mockRequest as any)
    vi.mocked(respondToPushRequest).mockResolvedValue({ status: 'ok' })
    
    render(<PatientApprovalScreen requestId={requestId} />)
    
    await waitFor(() => screen.getByText(/Deny/i))
    fireEvent.press(screen.getByText(/Deny/i))
    
    await waitFor(() => {
      expect(respondToPushRequest).toHaveBeenCalledWith(requestId, { decision: 'denied' })
      expect(screen.getByText(/Denied/i)).toBeTruthy()
    })
  })

  it('handles approve flow with biometrics', async () => {
    vi.mocked(getPushRequestStatus).mockResolvedValue(mockRequest as any)
    vi.mocked(respondToPushRequest).mockResolvedValue({ status: 'ok' })
    vi.mocked(LocalAuthentication.hasHardwareAsync).mockResolvedValue(true)
    vi.mocked(LocalAuthentication.isEnrolledAsync).mockResolvedValue(true)
    vi.mocked(LocalAuthentication.authenticateAsync).mockResolvedValue({ success: true } as any)
    
    render(<PatientApprovalScreen requestId={requestId} />)
    
    await waitFor(() => screen.getByText(/Approve/i))
    fireEvent.press(screen.getByText(/Approve/i))
    
    await waitFor(() => {
      expect(LocalAuthentication.authenticateAsync).toHaveBeenCalled()
      expect(respondToPushRequest).toHaveBeenCalledWith(requestId, expect.objectContaining({
        decision: 'approved',
        nonce: mockRequest.nonce
      }))
      expect(screen.getByText(/Approved/i)).toBeTruthy()
    })
  })

  it('auto-expires when countdown reaches zero', async () => {
    vi.useFakeTimers()
    const soonToExpire = {
      ...mockRequest,
      created_at: new Date(Date.now() - 88000).toISOString() // 2s left
    }
    vi.mocked(getPushRequestStatus).mockResolvedValue(soonToExpire as any)
    
    render(<PatientApprovalScreen requestId={requestId} />)
    
    await waitFor(() => expect(screen.queryByText(/Request Expired/i)).toBeNull())
    
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    
    expect(screen.getByText(/Request Expired/i)).toBeTruthy()
    vi.useRealTimers()
  })
})
