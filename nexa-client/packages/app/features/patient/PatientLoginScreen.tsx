import { useRouter } from 'expo-router'
import { YStack, H2, Paragraph, Input, Button, Spinner, Text } from 'tamagui'
import { useState } from 'react'
import { apiClient } from '../../utils/api'
import { setAuthTokenProvider } from '../../utils/api'

/**
 * ALPHA: Device signing flow scaffolded.
 * Real-device proof pending: secure hardware-backed key storage.
 * Do not claim hospital-grade biometric signing yet.
 */

interface PatientLoginScreenProps {
  /** Pre-filled phone number from deep-link or previous session */
  initialPhone?: string
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
      await apiClient.post('/api/v2/auth/otp/send', { phone }, { noAuth: true })
      setStep('otp')
    } catch {
      setError('Failed to send OTP. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const handleVerifyOtp = async () => {
    setLoading(true)
    setError(null)
    try {
      const { data } = await apiClient.post<{ jwt: string }>(
        '/api/v2/auth/otp/verify',
        { phone, otp },
        { noAuth: true },
      )
      // ALPHA: Store JWT via auth token provider.
      // Production: migrate to expo-secure-store / iOS Keychain.
      const jwt = data.jwt
      setAuthTokenProvider(() => jwt)

      // Check enrollment state — no hardcoded patient_id
      const { data: deviceData } = await apiClient.get<{ enrolled: boolean }>(
        '/api/v2/devices/status',
      )
      if (deviceData?.enrolled) {
        const { data: pendingData } = await apiClient.get<{ request_id?: string }>(
          '/api/v2/consent/requests/pending',
        )
        if (pendingData?.request_id) {
          router.replace({
            pathname: '/patient/consent-request',
            params: { requestId: pendingData.request_id },
          })
        } else {
          router.replace('/patient/access-history')
        }
      } else {
        router.replace('/patient/secure-device')
      }
    } catch {
      setError('Invalid OTP. Please try again.')
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

      {error && (
        <Text col="$red10" ta="center" size="$3">
          {error}
        </Text>
      )}
    </YStack>
  )
}
