import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient, type PatientConsentHistoryItem } from '../../utils/apiClient'
import PatientTreatmentAccessScreen from './PatientTreatmentAccessScreen'

const { push } = vi.hoisted(() => ({
  push: vi.fn(),
}))

vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push }),
}))

const mockGrants: PatientConsentHistoryItem[] = [
  {
    id: 'gref_v2_1111111111111111111111111111111111111111111111111111111111111111',
    public_ref: 'gref_v2_1111111111111111111111111111111111111111111111111111111111111111',
    purpose: 'Cardiology Treatment',
    status: 'active',
    scope: ['treatment'],
    issued_at: '2026-09-19T09:00:00Z',
    expires_at: '2026-09-19T09:30:00Z',
    revoked_at: null,
    type: 'routine',
    is_treatment_session: true,
  },
  {
    id: 'gref_v2_2222222222222222222222222222222222222222222222222222222222222222',
    public_ref: 'gref_v2_2222222222222222222222222222222222222222222222222222222222222222',
    purpose: 'Vitals Monitoring',
    status: 'expired',
    scope: ['treatment'],
    issued_at: '2026-09-19T08:00:00Z',
    expires_at: '2026-09-19T08:30:00Z',
    revoked_at: null,
    type: 'routine',
    is_treatment_session: true,
  },
  {
    id: 'gref_v2_3333333333333333333333333333333333333333333333333333333333333333',
    public_ref: 'gref_v2_3333333333333333333333333333333333333333333333333333333333333333',
    purpose: 'Post-op Observation',
    status: 'revoked',
    scope: ['treatment'],
    issued_at: '2026-09-19T07:00:00Z',
    expires_at: '2026-09-19T07:30:00Z',
    revoked_at: '2026-09-19T07:15:00Z',
    type: 'routine',
    is_treatment_session: true,
  },
  {
    // Non-treatment grant (should be filtered out of Treatment Access view)
    id: 'gref_v2_4444444444444444444444444444444444444444444444444444444444444444',
    public_ref: 'gref_v2_4444444444444444444444444444444444444444444444444444444444444444',
    purpose: 'General Record Read',
    status: 'active',
    scope: ['records.read'],
    issued_at: '2026-09-19T06:00:00Z',
    expires_at: '2026-09-19T06:30:00Z',
    revoked_at: null,
    type: 'routine',
    is_treatment_session: false,
  },
]

describe('PatientTreatmentAccessScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.spyOn(NexaApiClient, 'getSelfConsentHistory').mockResolvedValue(mockGrants)
    vi.spyOn(NexaApiClient, 'revokeSelfConsentGrant').mockResolvedValue({
      public_ref: mockGrants[0].public_ref,
      status: 'revoked',
      revoked_at: '2026-09-19T09:10:00Z',
    })
  })

  it('renders treatment access grants and filters out non-treatment grants', async () => {
    renderWithTamagui(<PatientTreatmentAccessScreen />)

    expect(await screen.findByText(/Cardiology Treatment/)).toBeTruthy()
    expect(screen.getByText(/Vitals Monitoring/)).toBeTruthy()
    expect(screen.getByText(/Post-op Observation/)).toBeTruthy()

    // Non-treatment purpose should not appear in this screen
    expect(screen.queryByText(/General Record Read/)).toBeNull()

    // Status badges
    expect(screen.getByText('ACTIVE')).toBeTruthy()
    expect(screen.getByText('EXPIRED')).toBeTruthy()
    expect(screen.getByText('REVOKED')).toBeTruthy()
  })

  it('renders Revoke Access button only for active grants', async () => {
    renderWithTamagui(<PatientTreatmentAccessScreen />)

    await screen.findByText(/Cardiology Treatment/)

    // Only 1 Revoke Access button for the 1 active treatment grant
    const revokeButtons = screen.getAllByRole('button', { name: /revoke.*access/i })
    expect(revokeButtons).toHaveLength(1)
  })

  it('prompts confirmation and successfully revokes an active grant', async () => {
    renderWithTamagui(<PatientTreatmentAccessScreen />)

    const revokeBtn = await screen.findByRole('button', { name: /revoke treatment access for cardiology treatment/i })
    fireEvent.click(revokeBtn)

    // Confirmation dialog appears
    expect(screen.getByText(/are you sure you want to revoke this treatment access\?/i)).toBeTruthy()
    const confirmBtn = screen.getByRole('button', { name: /confirm revoke treatment access/i })

    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(NexaApiClient.revokeSelfConsentGrant).toHaveBeenCalledWith(mockGrants[0].public_ref)
    })

    // Success alert is displayed
    expect(await screen.findByText('Treatment access successfully revoked.')).toBeTruthy()

    // History is refreshed from server
    expect(NexaApiClient.getSelfConsentHistory).toHaveBeenCalledTimes(2)
  })

  it('displays error and preserves state when revocation fails', async () => {
    vi.spyOn(NexaApiClient, 'revokeSelfConsentGrant').mockRejectedValueOnce(
      new Error('Network connectivity lost')
    )

    renderWithTamagui(<PatientTreatmentAccessScreen />)

    const revokeBtn = await screen.findByRole('button', { name: /revoke treatment access for cardiology treatment/i })
    fireEvent.click(revokeBtn)

    const confirmBtn = screen.getByRole('button', { name: /confirm revoke treatment access/i })
    fireEvent.click(confirmBtn)

    expect(await screen.findByText('Network connectivity lost')).toBeTruthy()

    // Grant is NOT optimistically marked revoked
    expect(screen.getByText('ACTIVE')).toBeTruthy()
  })

  it('navigates back to dashboard on button press', async () => {
    renderWithTamagui(<PatientTreatmentAccessScreen />)

    const backBtn = await screen.findByRole('button', { name: /back to dashboard/i })
    fireEvent.click(backBtn)

    expect(push).toHaveBeenCalledWith('/patient/dashboard')
  })
})
