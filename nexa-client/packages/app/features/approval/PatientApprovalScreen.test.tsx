import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { PatientApprovalScreen } from './PatientApprovalScreen'
import { renderWithTamagui } from '../../../../test/test-utils'
import { approveWithBiometric, denyWithSignature, fetchChallenge } from '../../services/consentSigning'

vi.mock('../../services/consentSigning', () => ({ fetchChallenge: vi.fn(), approveWithBiometric: vi.fn(), denyWithSignature: vi.fn(), isChallengeExpired: vi.fn(() => false) }))

const challenge = { request_id: 'req-123', patient_id: 'pat-1', provider_id: 'doc-1', provider_name: 'Doctor', hospital_name: 'Hospital', purpose: 'treatment', scope: 'clinical', access_duration: 900, challenge_nonce: 'nonce', expires_at: '2099-01-01T00:00:00Z', status: 'pending' }

beforeEach(() => { vi.clearAllMocks(); vi.mocked(fetchChallenge).mockResolvedValue(challenge) })

describe('PatientApprovalScreen', () => {
  it('fetches the canonical challenge and approves through consentSigning', async () => {
    vi.mocked(approveWithBiometric).mockResolvedValue({ request_id: 'req-123', status: 'approved', responded_at: 'now' })
    renderWithTamagui(<PatientApprovalScreen requestId="req-123" />)
    fireEvent.click(await screen.findByRole('button', { name: /approve with biometrics/i }))
    await waitFor(() => expect(approveWithBiometric).toHaveBeenCalledWith(challenge))
    expect(await screen.findByText('Approved')).toBeTruthy()
  })
  it('submits denial through the same canonical signing service', async () => {
    vi.mocked(denyWithSignature).mockResolvedValue({ request_id: 'req-123', status: 'denied', responded_at: 'now' })
    renderWithTamagui(<PatientApprovalScreen requestId="req-123" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Deny' }))
    await waitFor(() => expect(denyWithSignature).toHaveBeenCalledWith(challenge))
  })
  it('fails closed when the challenge cannot be fetched', async () => {
    vi.mocked(fetchChallenge).mockRejectedValue(new Error('missing'))
    renderWithTamagui(<PatientApprovalScreen requestId="req-123" />)
    expect(await screen.findByText('Operation Failed')).toBeTruthy()
    expect(approveWithBiometric).not.toHaveBeenCalled()
  })
})
