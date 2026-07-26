import { useRouter } from 'expo-router'
import { Keyboard, KeyboardAvoidingView, Platform, ScrollView } from 'react-native'
import { YStack, H2, Paragraph, Input, Button, Spinner, Text } from 'tamagui'
import { useRef, useState } from 'react'
import {
  CurrentDeviceError,
  ensureCurrentDeviceEnrollment,
} from '../../services/currentDeviceEnrollment'
import { storePatientAuthSession } from '../../services/patientAuthSession'
import { getRegisteredPushTokenForCurrentSession } from '../../services/pushNotifications'
import {
  patientAuthError,
  requestPatientOtp,
  tryBeginPatientOtpSubmission,
  verifyPatientOtp,
} from '../../services/patientOtp'

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
  const submissionInFlight = useRef(false)

  const handleSendOtp = async () => {
    if (!tryBeginPatientOtpSubmission(submissionInFlight)) return
    setLoading(true)
    setError(null)
    try {
      const normalizedPhone = await requestPatientOtp(phone)
      setPhone(normalizedPhone)
      setStep('otp')
    } catch (requestError) {
      setError(patientAuthError(requestError, 'Failed to send OTP. Please try again.'))
    } finally {
      submissionInFlight.current = false
      setLoading(false)
    }
  }

  const handleVerifyOtp = async () => {
    if (!tryBeginPatientOtpSubmission(submissionInFlight)) return
    setLoading(true)
    setError(null)
    try {
      const data = await verifyPatientOtp(phone, otp)
      await storePatientAuthSession(data.access_token, data.device_enrollment_token)

      // Enrollment is installation-specific: another active patient device
      // must never stand in for this installation's local key + device_id.
      await ensureCurrentDeviceEnrollment({
        expoPushToken: getRegisteredPushTokenForCurrentSession(),
      })
      Keyboard.dismiss()
      router.replace('/patient/access-history')
    } catch (requestError) {
      setError(
        requestError instanceof CurrentDeviceError
          ? requestError.message
          : patientAuthError(requestError, 'Unable to verify OTP. Please try again.')
      )
    } finally {
      submissionInFlight.current = false
      setLoading(false)
    }
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ flexGrow: 1 }}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
        showsVerticalScrollIndicator={false}
      >
        <YStack
          f={1}
          minHeight="100%"
          bg="$background"
          p="$4"
          gap="$4"
          jc="center"
          ai="center"
        >
          <H2
            col="$color"
            ta="center"
          >
            Welcome to Nexa Care
          </H2>
          <Paragraph
            col="$colorSubdued"
            ta="center"
            size="$5"
          >
            Your health data, under your control.
          </Paragraph>

          {step === 'phone' ? (
            <YStack
              w="100%"
              gap="$3"
              mt="$4"
            >
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
                {loading ? (
                  <Spinner
                    size="small"
                    color="$color"
                  />
                ) : (
                  'Send OTP'
                )}
              </Button>
            </YStack>
          ) : (
            <YStack
              w="100%"
              gap="$3"
              mt="$4"
            >
              <Paragraph
                col="$colorSubdued"
                ta="center"
                size="$3"
              >
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
                {loading ? (
                  <Spinner
                    size="small"
                    color="$color"
                  />
                ) : (
                  'Verify'
                )}
              </Button>
              <Button
                size="$3"
                chromeless
                onPress={() => setStep('phone')}
              >
                Change phone number
              </Button>
            </YStack>
          )}

          {error !== null ? (
            <Text
              col="$red10"
              ta="center"
              size="$3"
            >
              {error}
            </Text>
          ) : null}
        </YStack>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
