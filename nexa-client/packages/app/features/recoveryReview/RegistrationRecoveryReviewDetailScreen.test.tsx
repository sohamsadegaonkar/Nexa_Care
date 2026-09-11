import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import RegistrationRecoveryReviewDetailScreen from './RegistrationRecoveryReviewDetailScreen'
import {
  RegistrationRecoveryReviewClientError,
  claimReviewerCase,
  getReviewerCase,
  recoverReviewerSession,
  resolveReviewerCase,
} from '../../services/patientRegistrationRecoveryReview'

const { replace, push } = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }))
vi.mock('solito/navigation', () => ({ useRouter: () => ({ replace, push }) }))

vi.mock('../../services/patientRegistrationRecoveryReview', async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import('../../services/patientRegistrationRecoveryReview')
    >()
  return {
    ...actual,
    getReviewerCase: vi.fn(),
    claimReviewerCase: vi.fn(),
    recoverReviewerSession: vi.fn(),
    resolveReviewerCase: vi.fn(),
  }
})

const pendingCase = {
  case_reference: 'RRC-PENDING123456789012345',
  patient_id: null,
  status: 'PENDING' as const,
  reason_codes: ['MISSING_RECORD_ANCHOR' as const],
  version: 1,
  assigned_to_current_reviewer: false,
  created_at: '2026-09-11T10:00:00+00:00',
  claimed_at: null,
  resolved_at: null,
  contract_version: 'registration-recovery-review/1.0',
  policy_version: 'registration-recovery-review-policy/1.0',
}

const inReviewAssignedCase = {
  case_reference: 'RRC-INREVIEW12345678901234',
  patient_id: 'patient-uuid',
  status: 'IN_REVIEW' as const,
  reason_codes: ['MERGED_IDENTITY_REBIND_REQUIRED' as const],
  version: 2,
  assigned_to_current_reviewer: true,
  created_at: '2026-09-11T10:00:00+00:00',
  claimed_at: '2026-09-11T10:05:00+00:00',
  resolved_at: null,
  contract_version: 'registration-recovery-review/1.0',
  policy_version: 'registration-recovery-review-policy/1.0',
}

describe('RegistrationRecoveryReviewDetailScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('loads and renders case details', async () => {
    vi.mocked(getReviewerCase).mockResolvedValue(pendingCase)
    renderWithTamagui(
      <RegistrationRecoveryReviewDetailScreen caseReference="RRC-PENDING123456789012345" />
    )

    expect(await screen.findByText('Case RRC-PENDING123456789012345')).toBeVisible()
    expect(screen.getByText('PENDING')).toBeVisible()
    expect(screen.getByText('MISSING_RECORD_ANCHOR')).toBeVisible()
    expect(screen.getByText('v1')).toBeVisible()
  })

  it('allows claiming an unclaimed pending case with expected_version', async () => {
    vi.mocked(getReviewerCase).mockResolvedValue(pendingCase)
    vi.mocked(claimReviewerCase).mockResolvedValue({
      ...pendingCase,
      status: 'IN_REVIEW',
      version: 2,
      assigned_to_current_reviewer: true,
      claimed_at: '2026-09-11T10:05:00+00:00',
    })

    renderWithTamagui(
      <RegistrationRecoveryReviewDetailScreen caseReference="RRC-PENDING123456789012345" />
    )

    expect(await screen.findByRole('button', { name: 'Claim case' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Claim case' }))

    await waitFor(() => {
      expect(claimReviewerCase).toHaveBeenCalledWith('RRC-PENDING123456789012345', 1)
    })
    expect(await screen.findByText('Assigned to you')).toBeVisible()
  })

  it('handles claim version conflict by reloading authoritative case state', async () => {
    vi.mocked(getReviewerCase)
      .mockResolvedValueOnce(pendingCase)
      .mockResolvedValueOnce({
        ...pendingCase,
        version: 2,
        status: 'IN_REVIEW',
        assigned_to_current_reviewer: false,
      })

    vi.mocked(claimReviewerCase).mockRejectedValueOnce(
      new RegistrationRecoveryReviewClientError(
        'Version conflict',
        'REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT',
        409,
        false
      )
    )

    renderWithTamagui(
      <RegistrationRecoveryReviewDetailScreen caseReference="RRC-PENDING123456789012345" />
    )

    expect(await screen.findByRole('button', { name: 'Claim case' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Claim case' }))

    expect(await screen.findByText(/Case version conflict/i)).toBeVisible()
    expect(getReviewerCase).toHaveBeenCalledTimes(2)
  })

  it('renders resolution form for assigned in-review case and submits disposition with stable idempotency key', async () => {
    vi.mocked(getReviewerCase).mockResolvedValue(inReviewAssignedCase)
    vi.mocked(resolveReviewerCase).mockResolvedValue({
      ...inReviewAssignedCase,
      status: 'RESOLVED',
      version: 3,
      outcome: 'REBIND_MERGED_IDENTITY',
      resolved_at: '2026-09-11T10:15:00+00:00',
    })

    renderWithTamagui(
      <RegistrationRecoveryReviewDetailScreen caseReference="RRC-INREVIEW12345678901234" />
    )

    expect(await screen.findByText('Adjudication & Terminal Resolution')).toBeVisible()

    const outcomeRadio = screen.getByText('Rebind merged identity')
    fireEvent.click(outcomeRadio)

    const submitButton = screen.getByRole('button', { name: 'Submit terminal resolution' })
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(resolveReviewerCase).toHaveBeenCalledWith(
        'RRC-INREVIEW12345678901234',
        expect.objectContaining({
          expectedVersion: 2,
          outcome: 'REBIND_MERGED_IDENTITY',
          reasonCodes: ['MERGED_IDENTITY_REBIND_REQUIRED'],
          idempotencyKey: expect.stringMatching(/^rrr-resolve-/),
        })
      )
    })

    expect(await screen.findByText('Case disposition is terminal: RESOLVED')).toBeVisible()
  })

  it('handles repair not authorized error by showing explicit non-success notice', async () => {
    vi.mocked(getReviewerCase).mockResolvedValue(inReviewAssignedCase)
    vi.mocked(resolveReviewerCase).mockRejectedValueOnce(
      new RegistrationRecoveryReviewClientError(
        'Repair not authorized',
        'REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED',
        409,
        false
      )
    )

    renderWithTamagui(
      <RegistrationRecoveryReviewDetailScreen caseReference="RRC-INREVIEW12345678901234" />
    )

    expect(await screen.findByText('Adjudication & Terminal Resolution')).toBeVisible()

    const submitButton = screen.getByRole('button', { name: 'Submit terminal resolution' })
    fireEvent.click(submitButton)

    expect(
      await screen.findByText(/Repair outcome not authorized/i)
    ).toBeVisible()
  })

  it('handles session mismatch error and allows session recovery', async () => {
    vi.mocked(getReviewerCase).mockResolvedValue(inReviewAssignedCase)
    vi.mocked(resolveReviewerCase).mockRejectedValueOnce(
      new RegistrationRecoveryReviewClientError(
        'Session mismatch',
        'REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH',
        409,
        false
      )
    )
    vi.mocked(recoverReviewerSession).mockResolvedValueOnce({
      ...inReviewAssignedCase,
      version: 3,
    })

    renderWithTamagui(
      <RegistrationRecoveryReviewDetailScreen caseReference="RRC-INREVIEW12345678901234" />
    )

    expect(await screen.findByText('Adjudication & Terminal Resolution')).toBeVisible()

    const submitButton = screen.getByRole('button', { name: 'Submit terminal resolution' })
    fireEvent.click(submitButton)

    expect(await screen.findByText(/Session Re-binding Required/i)).toBeVisible()

    const recoverButton = screen.getByRole('button', { name: 'Recover review session' })
    fireEvent.click(recoverButton)

    await waitFor(() => {
      expect(recoverReviewerSession).toHaveBeenCalledWith('RRC-INREVIEW12345678901234', 2)
    })
  })
})
