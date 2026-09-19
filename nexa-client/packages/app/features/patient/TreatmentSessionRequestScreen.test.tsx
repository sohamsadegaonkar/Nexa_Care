import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import type { TreatmentSessionV1Challenge } from '../../utils/apiClient'
import TreatmentSessionRequestScreen from './TreatmentSessionRequestScreen'

const { replace, approve, deny } = vi.hoisted(() => ({
  replace: vi.fn(),
  approve: vi.fn(),
  deny: vi.fn(),
}))

vi.mock('expo-router', () => ({
  useRouter: () => ({ replace }),
  useLocalSearchParams: () => ({ requestId: '' }),
}))

vi.mock('../../services/treatmentSessionSigning', () => ({
  approveTreatmentSessionWithBiometric: approve,
  denyTreatmentSessionWithSignature: deny,
  fetchTreatmentSessionChallenge: vi.fn(),
  isTreatmentSessionChallengeExpired: () => false,
  classifyTreatmentSessionApprovalError: () => ({
    kind: 'retry',
    message: 'Unable to confirm request.',
  }),
}))

const challenge: TreatmentSessionV1Challenge = {
  protocol_version: 'nexa-treatment-session-v1',
  request_id: '11111111-1111-4111-8111-111111111111',
  patient_id: '22222222-2222-4222-8222-222222222222',
  provider_id: '33333333-3333-4333-8333-333333333333',
  hospital_id: '44444444-4444-4444-8444-444444444444',
  provider_name: 'Synthetic Provider',
  hospital_name: 'Synthetic Hospital',
  provider_session_binding_hash: 'b'.repeat(64),
  purpose: 'record_vitals',
  allowed_operations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
  access_duration: 900,
  challenge_nonce: 'do-not-render-nonce',
  issued_at: '2026-09-19T09:00:00+00:00',
  expires_at: '2099-09-19T09:02:00+00:00',
  treatment_context_hash: 'c'.repeat(64),
  status: 'pending',
}

describe('patient Treatment Session review', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    approve.mockResolvedValue({
      protocol_version: 'nexa-treatment-session-v1',
      request_id: challenge.request_id,
      status: 'approved',
      responded_at: '2026-09-19T09:01:00+00:00',
    })
    deny.mockResolvedValue({
      protocol_version: 'nexa-treatment-session-v1',
      request_id: challenge.request_id,
      status: 'denied',
      responded_at: '2026-09-19T09:01:00+00:00',
    })
  })

  it('shows provider and exact operations without exposing signing context', () => {
    renderWithTamagui(<TreatmentSessionRequestScreen initialChallenge={challenge} />)
    expect(screen.getByText('Synthetic Provider')).toBeTruthy()
    expect(screen.getByText('Synthetic Hospital')).toBeTruthy()
    expect(screen.getByText('CREATE_ENCOUNTER')).toBeTruthy()
    expect(screen.getByText('WRITE_VITALS')).toBeTruthy()
    expect(screen.queryByText(challenge.challenge_nonce)).toBeNull()
    expect(screen.queryByText(challenge.treatment_context_hash)).toBeNull()
    expect(screen.queryByText(challenge.provider_session_binding_hash)).toBeNull()
  })

  it('uses the treatment-specific biometric signer for approval', async () => {
    renderWithTamagui(<TreatmentSessionRequestScreen initialChallenge={challenge} />)
    fireEvent.click(screen.getByRole('button', { name: 'Approve with Biometrics' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(challenge))
    expect(await screen.findByText('Treatment Session approved')).toBeTruthy()
    const button = await screen.findByRole('button', { name: 'View Treatment Access' })
    fireEvent.click(button)
    expect(replace).toHaveBeenCalledWith('/patient/treatment-access')
  })

  it('denies through the treatment-specific signed denial path', async () => {
    renderWithTamagui(<TreatmentSessionRequestScreen initialChallenge={challenge} />)
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    await waitFor(() => expect(deny).toHaveBeenCalledWith(challenge))
    expect(await screen.findByText('Treatment Session denied')).toBeTruthy()
    const button = await screen.findByRole('button', { name: 'View Treatment Access' })
    fireEvent.click(button)
    expect(replace).toHaveBeenCalledWith('/patient/treatment-access')
  })
})
