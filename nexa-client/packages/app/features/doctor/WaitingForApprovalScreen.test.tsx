import { act, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError, NexaApiClient, type ConsentStatusResponse } from '../../utils/apiClient'
import { WaitingForApprovalScreen } from './WaitingForApprovalScreen'

const push = vi.fn()
const setAccessGrant = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => ({
    get: (key: string) => key === 'request_id' ? 'request-1' : key === 'patient_id' ? 'patient-1' : null,
  }),
}))

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => ({
    isAuthenticated: true,
    session: { hospital: { hospital_id: 'hospital-1' } },
    setAccessGrant,
  }),
}))

const statusResponse = (status: ConsentStatusResponse['status']): ConsentStatusResponse => ({
  request_id: 'request-1',
  status,
})

async function flushEffects() {
  await act(async () => { await Promise.resolve() })
}

describe('WaitingForApprovalScreen polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    push.mockReset()
    setAccessGrant.mockReset()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls pending status through one controlled timer loop with hospital context', async () => {
    const poll = vi.spyOn(NexaApiClient, 'getConsentStatus').mockResolvedValue(statusResponse('pending'))
    renderWithTamagui(<WaitingForApprovalScreen />)
    await flushEffects()

    expect(poll).toHaveBeenCalledOnce()
    expect(poll).toHaveBeenLastCalledWith('request-1', 'hospital-1')
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('stops on approval and navigates to the patient record', async () => {
    const poll = vi.spyOn(NexaApiClient, 'getConsentStatus').mockResolvedValue(statusResponse('approved'))
    const claim = vi.spyOn(NexaApiClient, 'claimConsentAccess').mockResolvedValue({
      patient_id: 'patient-verified',
      consent_token: 'secret-capability',
      purpose: 'treatment',
      scope: 'clinical',
      expires_at: '2099-01-01T00:00:00Z',
    })
    renderWithTamagui(<WaitingForApprovalScreen />)
    await flushEffects()
    expect(screen.getByText('Access Approved')).toBeTruthy()

    expect(claim).toHaveBeenCalledOnce()
    expect(claim).toHaveBeenCalledWith('request-1', 'hospital-1')
    expect(setAccessGrant).toHaveBeenCalledWith(expect.objectContaining({
      patientId: 'patient-verified',
      consentToken: 'secret-capability',
    }))
    expect(push).toHaveBeenCalledWith('/doctor/patient-record?patient_id=patient-verified')
    expect(push.mock.calls.flat().join(' ')).not.toContain('secret-capability')
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(poll).toHaveBeenCalledOnce()
    expect(claim).toHaveBeenCalledOnce()
  })

  it.each([
    ['denied', 'Access Denied'],
    ['expired', 'Request Expired'],
  ] as const)('stops polling for terminal %s status', async (status, heading) => {
    const poll = vi.spyOn(NexaApiClient, 'getConsentStatus').mockResolvedValue(statusResponse(status))
    renderWithTamagui(<WaitingForApprovalScreen />)
    await flushEffects()
    expect(screen.getByText(heading)).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(poll).toHaveBeenCalledOnce()
  })

  it.each([
    [401, 'Session Expired'],
    [403, 'Not Authorized'],
    [404, 'Request Expired'],
  ] as const)('stops polling after permanent HTTP %s', async (status, heading) => {
    const poll = vi.spyOn(NexaApiClient, 'getConsentStatus').mockRejectedValue(
      new ApiError('Permanent failure', status, 'API_ERROR', false),
    )
    renderWithTamagui(<WaitingForApprovalScreen />)
    await flushEffects()
    expect(screen.getByText(heading)).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(poll).toHaveBeenCalledOnce()
  })

  it('stops on 422 without calling it a network issue', async () => {
    const poll = vi.spyOn(NexaApiClient, 'getConsentStatus').mockRejectedValue(
      new ApiError('Invalid hospital UUID', 422, 'VALIDATION_ERROR', false),
    )
    renderWithTamagui(<WaitingForApprovalScreen />)
    await flushEffects()

    expect(screen.getByText('Status Check Failed')).toBeTruthy()
    expect(screen.getByText(/Consent status validation failed/)).toBeTruthy()
    expect(screen.queryByText(/Network issue/)).toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(poll).toHaveBeenCalledOnce()
  })

  it.each([
    [new ApiError('Offline', 0, 'NETWORK_ERROR', true), 'Network issue. Retrying...'],
    [new ApiError('Unavailable', 503, 'SERVER_ERROR', true), 'Server error. Retrying...'],
  ])('retries a retryable failure with controlled backoff', async (failure, message) => {
    const poll = vi.spyOn(NexaApiClient, 'getConsentStatus')
      .mockRejectedValueOnce(failure)
      .mockResolvedValue(statusResponse('pending'))
    renderWithTamagui(<WaitingForApprovalScreen />)
    await flushEffects()
    expect(screen.getByText(message)).toBeTruthy()

    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('clears the polling timer when unmounted', async () => {
    const poll = vi.spyOn(NexaApiClient, 'getConsentStatus').mockResolvedValue(statusResponse('pending'))
    const rendered = renderWithTamagui(<WaitingForApprovalScreen />)
    await flushEffects()
    rendered.unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(poll).toHaveBeenCalledOnce()
  })
})
