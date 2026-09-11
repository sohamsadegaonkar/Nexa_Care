import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import PatientRegistrationRecoveryScreen from './PatientRegistrationRecoveryScreen'
import {
  RegistrationRecoveryClientError,
  completePatientRegistrationRecovery,
  getPatientRegistrationRecoveryReviewStatus,
  requestPatientRegistrationRecoveryOtp,
  verifyPatientRegistrationRecoveryOtp,
} from '../../services/patientRegistrationRecovery'
import { ensureCurrentDeviceEnrollment } from '../../services/currentDeviceEnrollment'
import { storePatientAuthSession } from '../../services/patientAuthSession'

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))
vi.mock('solito/navigation', () => ({ useRouter: () => ({ replace }) }))
vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 24, bottom: 24, left: 0, right: 0 }),
}))
vi.mock('../../services/currentDeviceEnrollment', () => ({
  CurrentDeviceError: class CurrentDeviceError extends Error {
    constructor(
      message: string,
      public readonly code: string
    ) {
      super(message)
    }
  },
  ensureCurrentDeviceEnrollment: vi.fn(),
}))
vi.mock('../../services/patientAuthSession', () => ({
  storePatientAuthSession: vi.fn(),
}))
vi.mock('../../services/patientRegistrationRecovery', async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import('../../services/patientRegistrationRecovery')
    >()
  return {
    ...actual,
    requestPatientRegistrationRecoveryOtp: vi.fn(),
    verifyPatientRegistrationRecoveryOtp: vi.fn(),
    completePatientRegistrationRecovery: vi.fn(),
    getPatientRegistrationRecoveryReviewStatus: vi.fn(),
  }
})

async function sendRecoveryCode() {
  renderWithTamagui(<PatientRegistrationRecoveryScreen />)
  fireEvent.change(screen.getByLabelText('Phone number'), {
    target: { value: '+918000000001' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Send recovery OTP' }))
  return screen.findByLabelText('Verification code')
}

describe('patient registration account recovery', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(requestPatientRegistrationRecoveryOtp).mockResolvedValue({
      message: 'neutral',
      registration_recovery_attempt_token: 'attempt-token',
    })
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockResolvedValue({
      registration_recovery_token: 'repair-token',
      operation: 'repair_patient_registration_account',
      repair_kind: 'restore_patient_record_anchor',
      expires_in_seconds: 300,
      expires_at: '2099-01-01T00:05:00+00:00',
    })
    vi.mocked(completePatientRegistrationRecovery).mockResolvedValue({
      access_token: 'patient-access-token',
      token_type: 'bearer',
      expires_at: '2099-01-01T00:15:00+00:00',
      patient_id: 'patient-id',
      repair_kind: 'restore_patient_record_anchor',
      device_authority_state: 'bootstrap_enrollment',
      device_enrollment_token: 'enrollment-token',
      device_enrollment_expires_in_seconds: 300,
    })
    vi.mocked(getPatientRegistrationRecoveryReviewStatus).mockResolvedValue({
      case_reference: 'RRC-TEST123456789012345678',
      status: 'PENDING',
      terminal: false,
      next_action: 'WAIT_FOR_REVIEW',
      created_at: '2026-09-11T10:00:00+00:00',
      resolved_at: null,
    })
    vi.mocked(ensureCurrentDeviceEnrollment).mockResolvedValue({} as never)
  })

  it('uses the bounded attempt, repair capability, existing session store, and device reconciliation', async () => {
    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith('/patient/access-history')
    )
    expect(verifyPatientRegistrationRecoveryOtp).toHaveBeenCalledWith({
      phone: '+918000000001',
      otp: '123456',
      registrationRecoveryAttemptToken: 'attempt-token',
    })
    expect(completePatientRegistrationRecovery).toHaveBeenCalledWith('repair-token')
    expect(storePatientAuthSession).toHaveBeenCalledWith(
      'patient-access-token',
      'enrollment-token'
    )
    expect(ensureCurrentDeviceEnrollment).toHaveBeenCalledOnce()
  })

  it('captures case_reference, transitions to waiting state, and never mints session or enrolls device on manual review', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
        false,
        'RRC-TEST123456789012345678'
      )
    )
    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    expect(await screen.findByText('Account review in progress')).toBeVisible()
    expect(await screen.findByText('RRC-TEST123456789012345678')).toBeVisible()
    expect(completePatientRegistrationRecovery).not.toHaveBeenCalled()
    expect(storePatientAuthSession).not.toHaveBeenCalled()
    expect(ensureCurrentDeviceEnrollment).not.toHaveBeenCalled()
  })

  it('displays PENDING waiting state without exposing internal metadata', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
        false,
        'RRC-TEST123456789012345678'
      )
    )
    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    expect(await screen.findByText(/Account review: pending/i)).toBeVisible()
    expect(screen.queryByText(/graph_fingerprint/i)).toBeNull()
    expect(screen.queryByText(/reviewer_id/i)).toBeNull()
    expect(screen.queryByText(/policy_version/i)).toBeNull()
  })

  it('displays IN_REVIEW state when review is claimed', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
        false,
        'RRC-TEST123456789012345678'
      )
    )
    vi.mocked(getPatientRegistrationRecoveryReviewStatus).mockResolvedValue({
      case_reference: 'RRC-TEST123456789012345678',
      status: 'IN_REVIEW',
      terminal: false,
      next_action: 'WAIT_FOR_REVIEW',
      created_at: '2026-09-11T10:00:00+00:00',
      resolved_at: null,
    })

    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    await waitFor(() => {
      expect(screen.getByText(/Account review: in review/i)).toBeVisible()
    })
  })

  it('handles RESOLVED + RESTART_ACCOUNT_RECOVERY and allows restarting from phone step', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
        false,
        'RRC-TEST123456789012345678'
      )
    )
    vi.mocked(getPatientRegistrationRecoveryReviewStatus).mockResolvedValue({
      case_reference: 'RRC-TEST123456789012345678',
      status: 'RESOLVED',
      terminal: true,
      next_action: 'RESTART_ACCOUNT_RECOVERY',
      created_at: '2026-09-11T10:00:00+00:00',
      resolved_at: '2026-09-11T10:05:00+00:00',
    })

    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    expect(await screen.findByText('Account review resolved')).toBeVisible()
    expect(screen.queryByRole('button', { name: /Verify and repair account/i })).toBeNull()
    expect(storePatientAuthSession).not.toHaveBeenCalled()

    const restartButton = screen.getByRole('button', {
      name: 'Restart account recovery',
    })
    fireEvent.click(restartButton)

    expect(await screen.findByLabelText('Phone number')).toBeVisible()
  })

  it('handles REJECTED terminal review state with contact support instructions and no repair CTA', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
        false,
        'RRC-TEST123456789012345678'
      )
    )
    vi.mocked(getPatientRegistrationRecoveryReviewStatus).mockResolvedValue({
      case_reference: 'RRC-TEST123456789012345678',
      status: 'REJECTED',
      terminal: true,
      next_action: 'CONTACT_SUPPORT',
      created_at: '2026-09-11T10:00:00+00:00',
      resolved_at: '2026-09-11T10:05:00+00:00',
    })

    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    expect(await screen.findByText('Account recovery rejected')).toBeVisible()
    expect(screen.queryByRole('button', { name: /repair/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Restart account recovery/i })).toBeNull()
  })

  it('handles SECURITY_ESCALATED terminal state with security escalation notice', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
        false,
        'RRC-TEST123456789012345678'
      )
    )
    vi.mocked(getPatientRegistrationRecoveryReviewStatus).mockResolvedValue({
      case_reference: 'RRC-TEST123456789012345678',
      status: 'SECURITY_ESCALATED',
      terminal: true,
      next_action: 'CONTACT_SUPPORT',
      created_at: '2026-09-11T10:00:00+00:00',
      resolved_at: '2026-09-11T10:05:00+00:00',
    })

    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    expect(await screen.findByText('Account security escalation')).toBeVisible()
    expect(screen.queryByRole('button', { name: /repair/i })).toBeNull()
  })

  it('retains case_reference when manual check status returns temporary error', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
        false,
        'RRC-TEST123456789012345678'
      )
    )
    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    expect(await screen.findByText('RRC-TEST123456789012345678')).toBeVisible()

    vi.mocked(getPatientRegistrationRecoveryReviewStatus).mockRejectedValueOnce(
      new Error('Temporary timeout')
    )

    const checkButton = screen.getByRole('button', { name: 'Check status' })
    fireEvent.click(checkButton)

    expect(await screen.findByText('Temporary timeout')).toBeVisible()
    expect(screen.getByText('RRC-TEST123456789012345678')).toBeVisible()
  })

  it('routes an account with historical device authority into the separate device recovery flow', async () => {
    const deviceRecoveryError = Object.assign(new Error('recover device'), {
      code: 'RECOVERY_REQUIRED',
    })
    Object.setPrototypeOf(
      deviceRecoveryError,
      (await import('../../services/currentDeviceEnrollment')).CurrentDeviceError
        .prototype
    )
    vi.mocked(ensureCurrentDeviceEnrollment).mockRejectedValue(
      deviceRecoveryError
    )

    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith('/patient/recovery')
    )
    expect(storePatientAuthSession).toHaveBeenCalledWith(
      'patient-access-token',
      'enrollment-token'
    )
  })

  it('restarts from phone proof when the repair graph changes', async () => {
    vi.mocked(verifyPatientRegistrationRecoveryOtp).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'The account changed while recovery was in progress. Restart account recovery.',
        'state_changed',
        'REGISTRATION_RECOVERY_STATE_CHANGED',
        false
      )
    )
    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    expect(await screen.findByLabelText('Phone number')).toBeVisible()
    expect(completePatientRegistrationRecovery).not.toHaveBeenCalled()
  })

  it('falls back to ordinary sign-in when repair committed but session authority failed', async () => {
    vi.mocked(completePatientRegistrationRecovery).mockRejectedValue(
      new RegistrationRecoveryClientError(
        'The account repair completed, but a patient session could not be established. Sign in normally with a fresh OTP.',
        'sign_in_required',
        'PATIENT_SESSION_AUTHORITY_UNAVAILABLE',
        false
      )
    )
    const code = await sendRecoveryCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(
      screen.getByRole('button', { name: 'Verify and repair account' })
    )

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith('/patient/login')
    )
    expect(storePatientAuthSession).not.toHaveBeenCalled()
    expect(ensureCurrentDeviceEnrollment).not.toHaveBeenCalled()
  })
})

