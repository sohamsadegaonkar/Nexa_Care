import { useRouter } from 'expo-router'
import { YStack, H2, Paragraph, Input, Button, Spinner, Text } from 'tamagui'
import { useState } from 'react'
import { ApiError, apiClient } from '../../utils/apiClient'
import { getDevices, storePatientAuthSession } from '../../services/deviceKeys'

/**
 * ALPHA: Device signing flow scaffolded.
 * Real-device proof pending: secure hardware-backed key storage.
 * Do not claim hospital-grade biometric signing yet.
 */

interface PatientLoginScreenProps {
  /** Pre-filled phone number from deep-link or previous session */
  initialPhone?: string
}

interface PatientOtpVerifyResponse {
  access_token: string
  token_type: 'bearer'
  expires_at: string
  device_enrollment_token: string
}

function normalizePhoneInput(value: string): string {
  const digits = value.replace(/\D/g, '')
  if (digits.length === 10) return `+91${digits}`
  if (digits.length === 12 && digits.startsWith('91')) return `+${digits}`
  return value.trim()
}

function patientAuthError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback
  if (error.status === 0) return 'Cannot reach Nexa Care. Check your connection and try again.'
  if (error.status === 401) return 'The OTP is invalid or expired. Request a new code and try again.'
  if (error.status === 403) return 'No patient account is linked to this verified phone number.'
  if (error.status === 429) return 'Too many attempts. Please wait a few minutes and try again.'
  if (error.status === 503) return 'The SMS service is temporarily unavailable. Please try again later.'
  return fallback
}

export default function PatientLoginScreen({ initialPhone = '' }: PatientLoginScreenProps) {
  const router = useRouter()
  const [phone, setPhone] = useState(initialPhone)
  const [otp, setOtp] = useState('')
  const [step, setStep] = useState<'phone' | 'otp'>('phone')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSendOtp = async () => {
    setLoading(true)
    setError(null)
    try {
      const normalizedPhone = normalizePhoneInput(phone)
      await apiClient.post('/api/v2/auth/otp/send', { phone: normalizedPhone }, { noAuth: true })
      setPhone(normalizedPhone)
      setStep('otp')
    } catch (requestError) {
      setError(patientAuthError(requestError, 'Failed to send OTP. Please try again.'))
    } finally {
      setLoading(false)
    }
  }

  const handleVerifyOtp = async () => {
    setLoading(true)
    setError(null)
    try {
      const { data } = await apiClient.post<PatientOtpVerifyResponse>(
        '/api/v2/auth/otp/verify',
        { phone, otp },
        { noAuth: true },
      )
      await storePatientAuthSession(data.access_token, data.device_enrollment_token)

      const deviceData = await getDevices()
      if (deviceData.devices.some((device) => device.status === 'active')) {
        router.replace('/patient/access-history')
      } else {
        router.replace('/patient/secure-device')
      }
    } catch (requestError) {
      setError(patientAuthError(requestError, 'Unable to verify OTP. Please try again.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <YStack f={1} bg="$background" p="$4" gap="$4" jc="center" ai="center">
      <H2 col="$color" ta="center">
        Welcome to Nexa Care
      </H2>
      <Paragraph col="$colorSubdued" ta="center" size="$5">
        Your health data, under your control.
      </Paragraph>

      {step === 'phone' ? (
        <YStack w="100%" gap="$3" mt="$4">
          <Input
            placeholder="Phone number"
            value={phone}
            onChangeText={setPhone}
            keyboardType="phone-pad"
            size="$4"
            autoCapitalize="none"
          />
          <Button
            theme="blue"
            size="$4"
            disabled={!phone || loading}
            onPress={handleSendOtp}
          >
            {loading ? <Spinner size="small" color="$color" /> : 'Send OTP'}
          </Button>
        </YStack>
      ) : (
        <YStack w="100%" gap="$3" mt="$4">
          <Paragraph col="$colorSubdued" ta="center" size="$3">
            Enter the 6-digit code sent to {phone}
          </Paragraph>
          <Input
            placeholder="OTP"
            value={otp}
            onChangeText={setOtp}
            keyboardType="number-pad"
            maxLength={6}
            size="$4"
          />
          <Button
            theme="blue"
            size="$4"
            disabled={otp.length < 6 || loading}
            onPress={handleVerifyOtp}
          >
            {loading ? <Spinner size="small" color="$color" /> : 'Verify'}
          </Button>
          <Button size="$3" chromeless onPress={() => setStep('phone')}>
            Change phone number
          </Button>
        </YStack>
      )}

      {error !== null ? (
        <Text col="$red10" ta="center" size="$3">
          {error}
        </Text>
      ) : null}
    </YStack>
  )
}
