import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import PatientLoginScreen from './PatientLoginScreen'
import { requestPatientOtp, verifyPatientOtp } from '../../services/patientOtp'
import { ensureCurrentDeviceEnrollment } from '../../services/currentDeviceEnrollment'
import { storePatientAuthSession } from '../../services/patientAuthSession'

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))
vi.mock('expo-router', () => ({ useRouter: () => ({ replace }) }))
vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 24, bottom: 24, left: 0, right: 0 }),
}))
vi.mock('../../services/currentDeviceEnrollment', () => ({
  CurrentDeviceError: class extends Error {},
  ensureCurrentDeviceEnrollment: vi.fn(),
}))
vi.mock('../../services/patientAuthSession', () => ({ storePatientAuthSession: vi.fn() }))
vi.mock('../../services/pushNotifications', () => ({
  getRegisteredPushTokenForCurrentSession: () => null,
}))
vi.mock('../../services/patientOtp', () => ({
  requestPatientOtp: vi.fn(),
  verifyPatientOtp: vi.fn(),
  patientAuthError: (_error: unknown, message: string) => message,
  tryBeginPatientOtpSubmission: (ref: { current: boolean }) => {
    if (ref.current) return false
    ref.current = true
    return true
  },
}))

async function sendCode() {
  renderWithTamagui(<PatientLoginScreen />)
  fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '+910000000000' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send OTP' }))
  return screen.findByLabelText('Verification code')
}

describe('patient sign-in presentation', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(requestPatientOtp).mockResolvedValue('+910000000000')
    vi.mocked(ensureCurrentDeviceEnrollment).mockResolvedValue({} as never)
  })

  it('clears the code when changing phone and preserves the two-step journey', async () => {
    const code = await sendCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change phone number' }))
    expect(screen.getByLabelText('Phone number')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Send OTP' }))
    expect(await screen.findByLabelText('Verification code')).toHaveValue('')
  })

  it('uses the existing session and enrollment path before navigating', async () => {
    vi.mocked(verifyPatientOtp).mockResolvedValue({
      access_token: 'synthetic-access',
      device_enrollment_token: 'synthetic-enrollment',
    } as never)
    const code = await sendCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify', exact: true }))
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/patient/access-history'))
    expect(storePatientAuthSession).toHaveBeenCalledWith('synthetic-access', 'synthetic-enrollment')
    expect(ensureCurrentDeviceEnrollment).toHaveBeenCalledWith({ expoPushToken: null })
  })

  it('announces verification failure and stays on the code step', async () => {
    vi.mocked(verifyPatientOtp).mockRejectedValue(new Error('synthetic failure'))
    const code = await sendCode()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify', exact: true }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to verify OTP. Please try again.'
    )
    expect(screen.getByLabelText('Verification code')).toBeVisible()
    expect(replace).not.toHaveBeenCalled()
  })
})
