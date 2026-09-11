import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import RegistrationRecoveryReviewQueueScreen from './RegistrationRecoveryReviewQueueScreen'
import {
  RegistrationRecoveryReviewClientError,
  listReviewerCases,
} from '../../services/patientRegistrationRecoveryReview'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('solito/navigation', () => ({ useRouter: () => ({ push }) }))

vi.mock('../../services/patientRegistrationRecoveryReview', async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import('../../services/patientRegistrationRecoveryReview')
    >()
  return {
    ...actual,
    listReviewerCases: vi.fn(),
  }
})

const mockCases = [
  {
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
  },
  {
    case_reference: 'RRC-INREVIEW12345678901234',
    patient_id: '11111111-1111-4111-8111-111111111111',
    status: 'IN_REVIEW' as const,
    reason_codes: ['MERGED_IDENTITY_REBIND_REQUIRED' as const],
    version: 2,
    assigned_to_current_reviewer: true,
    created_at: '2026-09-11T10:05:00+00:00',
    claimed_at: '2026-09-11T10:10:00+00:00',
    resolved_at: null,
    contract_version: 'registration-recovery-review/1.0',
    policy_version: 'registration-recovery-review-policy/1.0',
  },
]

describe('RegistrationRecoveryReviewQueueScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('renders loading state initially', async () => {
    vi.mocked(listReviewerCases).mockReturnValue(new Promise(() => {}))
    renderWithTamagui(<RegistrationRecoveryReviewQueueScreen />)

    expect(screen.getByText(/Loading reviewer cases/i)).toBeVisible()
  })

  it('renders populated queue with pending and assigned cases', async () => {
    vi.mocked(listReviewerCases).mockResolvedValue({ cases: mockCases })
    renderWithTamagui(<RegistrationRecoveryReviewQueueScreen />)

    expect(await screen.findByText('RRC-PENDING123456789012345')).toBeVisible()
    expect(screen.getByText('RRC-INREVIEW12345678901234')).toBeVisible()
    expect(screen.getByText('Unclaimed')).toBeVisible()
    expect(screen.getByText('Assigned to you')).toBeVisible()
    expect(screen.getByText('MISSING_RECORD_ANCHOR')).toBeVisible()
    expect(screen.getByText('MERGED_IDENTITY_REBIND_REQUIRED')).toBeVisible()
  })

  it('renders empty queue state when no cases returned', async () => {
    vi.mocked(listReviewerCases).mockResolvedValue({ cases: [] })
    renderWithTamagui(<RegistrationRecoveryReviewQueueScreen />)

    expect(await screen.findByText('No review cases found')).toBeVisible()
  })

  it('navigates to case detail when clicking view case', async () => {
    vi.mocked(listReviewerCases).mockResolvedValue({ cases: mockCases })
    const onSelectCase = vi.fn()
    renderWithTamagui(
      <RegistrationRecoveryReviewQueueScreen onSelectCase={onSelectCase} />
    )

    expect(await screen.findByText('RRC-PENDING123456789012345')).toBeVisible()
    const viewButtons = screen.getAllByRole('button', { name: 'View case' })
    fireEvent.click(viewButtons[0])

    expect(onSelectCase).toHaveBeenCalledWith('RRC-PENDING123456789012345')
  })

  it('handles access denied error with error banner', async () => {
    vi.mocked(listReviewerCases).mockRejectedValue(
      new RegistrationRecoveryReviewClientError(
        'Reviewer role required',
        'REGISTRATION_RECOVERY_REVIEW_ROLE_REQUIRED',
        403,
        false
      )
    )
    renderWithTamagui(<RegistrationRecoveryReviewQueueScreen />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Reviewer role required')
  })

  it('filters cases when selecting status tab', async () => {
    vi.mocked(listReviewerCases).mockResolvedValue({ cases: [mockCases[0]] })
    renderWithTamagui(<RegistrationRecoveryReviewQueueScreen />)

    expect(await screen.findByText('RRC-PENDING123456789012345')).toBeVisible()

    const pendingTab = screen.getByRole('button', { name: 'Pending' })
    fireEvent.click(pendingTab)

    await waitFor(() => {
      expect(listReviewerCases).toHaveBeenCalledWith({ status: 'PENDING', limit: 50 })
    })
  })
})
