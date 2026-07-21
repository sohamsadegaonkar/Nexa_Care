import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'
import { MergeAdminScreen } from './MergeAdminScreen'
import * as authApi from '../../api/auth'
import * as mergeApi from '../../api/merge'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError } from '../../utils/apiClient'

vi.mock('../../api/auth', () => ({
  cancelMergeChallenge: vi.fn(),
  createMergeChallenge: vi.fn(),
  verifyMergeChallenge: vi.fn(),
}))

vi.mock('../../api/merge', () => ({
  mergePatients: vi.fn(),
  MergeError: class extends Error {
    constructor(message: string) {
      super(message)
    }
  },
}))

describe('MergeAdminScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders initiate merge state', () => {
    renderWithTamagui(<MergeAdminScreen />)
    expect(screen.getByText(/Patient Merge \(Admin\)/i)).toBeTruthy()
    expect(screen.getByPlaceholderText(/Old Patient UUID/i)).toBeTruthy()
  })

  it('shows MFA sheet when initiate merge is clicked', async () => {
    vi.mocked(authApi.createMergeChallenge).mockResolvedValue({
      challenge_token: 'challenge-123',
      requires_mfa: true,
      expires_in_seconds: 120,
    })

    renderWithTamagui(<MergeAdminScreen />)

    fireEvent.change(screen.getByPlaceholderText(/Old Patient UUID/i), { target: { value: 'old-uuid' } })
    fireEvent.change(screen.getByPlaceholderText(/Canonical Patient UUID/i), { target: { value: 'new-uuid' } })
    fireEvent.change(screen.getByPlaceholderText(/Reason for merge/i), { target: { value: 'test reason' } })

    fireEvent.click(screen.getByRole('button', { name: /INITIATE MERGE/i }))

    await waitFor(() => {
      expect(authApi.createMergeChallenge).toHaveBeenCalled()
      expect(screen.getByText(/MFA Verification Required/i)).toBeTruthy()
    })
  })

  it('executes merge after successful MFA verification', async () => {
    vi.mocked(authApi.createMergeChallenge).mockResolvedValue({
      challenge_token: 'challenge-123',
      requires_mfa: true,
      expires_in_seconds: 120,
    })
    vi.mocked(authApi.verifyMergeChallenge).mockResolvedValue({
      challenge_token: 'challenge-123',
      verified: true,
    })
    vi.mocked(mergeApi.mergePatients).mockResolvedValue({
      message: 'success',
      tombstone_id: 'tomb-123',
      canonical_patient_uuid: 'new-uuid',
    })

    renderWithTamagui(<MergeAdminScreen />)

    fireEvent.change(screen.getByPlaceholderText(/Old Patient UUID/i), { target: { value: 'old-uuid' } })
    fireEvent.change(screen.getByPlaceholderText(/Canonical Patient UUID/i), { target: { value: 'new-uuid' } })
    fireEvent.change(screen.getByPlaceholderText(/Reason for merge/i), { target: { value: 'test reason' } })

    fireEvent.click(screen.getByRole('button', { name: /INITIATE MERGE/i }))

    await waitFor(() => screen.getByPlaceholderText('123456'))

    fireEvent.change(screen.getByPlaceholderText('123456'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: /Verify & Execute/i }))

    await waitFor(() => {
      expect(authApi.verifyMergeChallenge).toHaveBeenCalledWith('challenge-123', '123456')
      expect(mergeApi.mergePatients).toHaveBeenCalledWith(expect.anything(), 'challenge-123')
      expect(screen.getByText(/Merge Successful/i)).toBeTruthy()
    })
  })

  it('shows error on failed MFA verification', async () => {
    vi.mocked(authApi.createMergeChallenge).mockResolvedValue({
      challenge_token: 'challenge-123',
      requires_mfa: true,
      expires_in_seconds: 120,
    })
    vi.mocked(authApi.verifyMergeChallenge).mockRejectedValue(
      new ApiError('Invalid code', 401, 'MFA_INVALID')
    )

    renderWithTamagui(<MergeAdminScreen />)

    fireEvent.change(screen.getByPlaceholderText(/Old Patient UUID/i), { target: { value: 'old-uuid' } })
    fireEvent.change(screen.getByPlaceholderText(/Canonical Patient UUID/i), { target: { value: 'new-uuid' } })
    fireEvent.change(screen.getByPlaceholderText(/Reason for merge/i), { target: { value: 'test reason' } })

    fireEvent.click(screen.getByRole('button', { name: /INITIATE MERGE/i }))

    await waitFor(() => screen.getByPlaceholderText('123456'))

    fireEvent.change(screen.getByPlaceholderText('123456'), { target: { value: '000000' } })
    fireEvent.click(screen.getByRole('button', { name: /Verify & Execute/i }))

    await waitFor(() => {
      expect(screen.getAllByText(/MFA verification failed/i).length).toBeGreaterThan(0)
      expect(screen.getByText(/MFA Verification Required/i)).toBeTruthy()
    })
  })
})
