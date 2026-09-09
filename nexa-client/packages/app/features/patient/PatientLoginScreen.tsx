import { useRouter } from 'expo-router'
import { Keyboard, KeyboardAvoidingView, Platform, ScrollView } from 'react-native'
import {
  ActionButton,
  AuthFrame,
  FormField,
  InlineNotice,
  Paragraph,
  ScreenHeader,
  StatusBadge,
  YStack,
} from '@my/ui'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
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
  const insets = useSafeAreaInsets()
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
        <AuthFrame
          paddingTop={insets.top + 24}
          paddingBottom={insets.bottom + 24}
        >
          <StatusBadge tone="info">
            {step === 'phone' ? 'Step 1 of 2: Your phone' : 'Step 2 of 2: Verify'}
          </StatusBadge>
          <ScreenHeader
            title={step === 'phone' ? 'Your care, connected' : 'Check your messages'}
            description={
              step === 'phone'
                ? 'Sign in to see who accessed your records and manage access requests.'
                : 'Enter the 6-digit code sent to your phone.'
            }
          />
          <YStack gap="$4">
            {step === 'phone' ? (
              <FormField
                id="patient-phone"
                label="Phone number"
                placeholder="Phone number"
                hint="Include your country code."
                value={phone}
                onChangeText={setPhone}
                keyboardType="phone-pad"
                autoComplete="tel"
                autoCapitalize="none"
                autoCorrect={false}
                disabled={loading}
                returnKeyType="done"
                onSubmitEditing={handleSendOtp}
              />
            ) : (
              <FormField
                key="otp"
                id="patient-otp"
                label="Verification code"
                placeholder="OTP"
                value={otp}
                onChangeText={setOtp}
                keyboardType="number-pad"
                inputMode="numeric"
                autoComplete="one-time-code"
                textContentType="oneTimeCode"
                autoFocus
                maxLength={6}
                letterSpacing={6}
                fontSize={24}
                textAlign="center"
                disabled={loading}
                onSubmitEditing={handleVerifyOtp}
              />
            )}
            {error !== null && (
              <InlineNotice
                title={error}
                tone="danger"
              />
            )}
            <ActionButton
              intent="primary"
              disabled={loading || (step === 'phone' ? !phone : otp.length < 6)}
              aria-busy={loading}
              onPress={step === 'phone' ? handleSendOtp : handleVerifyOtp}
            >
              {loading
                ? step === 'phone'
                  ? 'Sending code...'
                  : 'Verifying...'
                : step === 'phone'
                  ? 'Send OTP'
                  : 'Verify'}
            </ActionButton>
            {loading && (
              <Paragraph
                aria-live="polite"
                color="$nexaSecondary"
              >
                Please wait...
              </Paragraph>
            )}
            {step === 'otp' && (
              <ActionButton
                disabled={loading}
                onPress={() => {
                  setStep('phone')
                  setOtp('')
                  setError(null)
                }}
              >
                Change phone number
              </ActionButton>
            )}
          </YStack>
          <Paragraph
            color="$nexaSecondary"
            fontSize={14}
            lineHeight={22}
          >
            Signing in does not give a provider access to your records. You review routine access
            requests separately.
          </Paragraph>
        </AuthFrame>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
