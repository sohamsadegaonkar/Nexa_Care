import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import ConsentRequestScreen from './ConsentRequestScreen'
import { denyWithSignature, type ConsentChallenge } from '../../services/consentSigning'

const { push, replace, reset } = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  reset: vi.fn(),
}))
vi.mock('expo-router', () => ({
  useRouter: () => ({ push, replace }),
  useLocalSearchParams: () => ({ requestId: 'request-synthetic' }),
}))
vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 24, left: 0, right: 0 }),
}))
vi.mock('../../hooks/useResetToPatientAccessHistory', () => ({
  useResetToPatientAccessHistory: () => reset,
}))
vi.mock('../../services/consentSigning', () => ({
  denyWithSignature: vi.fn(),
  fetchChallenge: vi.fn(),
  isChallengeExpired: vi.fn(),
  classifyConsentError: vi.fn(),
}))

const challenge: ConsentChallenge = {
  protocol_version: 'nexa-consent-v3',
  request_id: 'request-synthetic',
  patient_id: 'patient-synthetic',
  provider_id: 'provider-synthetic',
  hospital_id: 'facility-synthetic',
  provider_name: 'Synthetic Provider',
  hospital_name: 'Synthetic Facility',
  purpose: 'treatment',
  scope: 'clinical_summary,lab_results',
  access_duration: 900,
  challenge_nonce: 'synthetic-nonce',
  issued_at: '2099-01-01T00:00:00Z',
  expires_at: '2099-01-01T00:02:00Z',
  consent_context_hash: 'synthetic-context',
  status: 'pending',
}

describe('consent review presentation preserves the signing boundary', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('shows requester, purpose, exact scope, access duration and a separate response timer', () => {
    renderWithTamagui(<ConsentRequestScreen initialChallenge={challenge} />)
    for (const text of [
      'Synthetic Provider',
      'Synthetic Facility',
      'treatment',
      'clinical_summary',
      'lab_results',
      '15 minutes',
      'Request Expires In',
    ]) {
      expect(screen.getByText(text)).toBeVisible()
    }
    expect(screen.queryByText(challenge.challenge_nonce)).toBeNull()
    expect(screen.queryByText(challenge.consent_context_hash)).toBeNull()
  })

  it('approval navigates to biometric verification using only the request identifier', () => {
    renderWithTamagui(<ConsentRequestScreen initialChallenge={challenge} />)
    fireEvent.click(screen.getByRole('button', { name: 'Approve', exact: true }))
    expect(push).toHaveBeenCalledWith({
      pathname: '/patient/biometric-approval',
      params: { requestId: challenge.request_id },
    })
    expect(denyWithSignature).not.toHaveBeenCalled()
  })

  it('denial retains the complete challenge and waits for signed success', async () => {
    vi.mocked(denyWithSignature).mockResolvedValue({
      request_id: challenge.request_id,
      status: 'denied',
      responded_at: '2099-01-01T00:01:00Z',
    })
    renderWithTamagui(<ConsentRequestScreen initialChallenge={challenge} />)
    fireEvent.click(screen.getByRole('button', { name: 'Deny', exact: true }))
    expect(denyWithSignature).toHaveBeenCalledWith(challenge)
    await waitFor(() => expect(reset).toHaveBeenCalledOnce())
  })

  it('announces failed denial without claiming success or navigating away', async () => {
    vi.mocked(denyWithSignature).mockRejectedValue(new Error('synthetic failure'))
    renderWithTamagui(<ConsentRequestScreen initialChallenge={challenge} />)
    fireEvent.click(screen.getByRole('button', { name: 'Deny', exact: true }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Failed to deny request. Please try again.'
    )
    expect(reset).not.toHaveBeenCalled()
  })

  it('removes approval actions for an expired challenge', () => {
    renderWithTamagui(
      <ConsentRequestScreen
        initialChallenge={{ ...challenge, expires_at: '2000-01-01T00:00:00Z' }}
      />
    )
    expect(screen.getByText('Request Expired')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Approve', exact: true })).toBeNull()
    expect(denyWithSignature).not.toHaveBeenCalled()
  })
})
