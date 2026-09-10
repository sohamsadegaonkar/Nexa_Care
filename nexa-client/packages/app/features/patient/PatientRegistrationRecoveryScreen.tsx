import { useRouter } from 'expo-router'
import { Keyboard, KeyboardAvoidingView, Platform, ScrollView } from 'react-native'
import { useState } from 'react'
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
import {
  CurrentDeviceError,
  ensureCurrentDeviceEnrollment,
} from '../../services/currentDeviceEnrollment'
import { storePatientAuthSession } from '../../services/patientAuthSession'
import {
  RegistrationRecoveryClientError,
  completePatientRegistrationRecovery,
  requestPatientRegistrationRecoveryOtp,
  verifyPatientRegistrationRecoveryOtp,
} from '../../services/patientRegistrationRecovery'

export default function PatientRegistrationRecoveryScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState('')
  const [attemptToken, setAttemptToken] = useState<string | null>(null)
  const [step, setStep] = useState<'phone' | 'otp' | 'repairing'>('phone')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const restart = () => {
    setOtp('')
    setAttemptToken(null)
    setError(null)
    setStep('phone')
  }

  const sendOtp = async () => {
    if (busy || !phone.trim()) return
    setBusy(true)
    setError(null)
    try {
      const response = await requestPatientRegistrationRecoveryOtp(phone.trim())
      setAttemptToken(response.registration_recovery_attempt_token)
      setStep('otp')
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Unable to start account recovery.'
      )
    } finally {
      setBusy(false)
    }
  }

  const repairAccount = async () => {
    if (busy || !attemptToken || otp.trim().length !== 6) return
    setBusy(true)
    setError(null)
    setStep('repairing')
    try {
      const capability = await verifyPatientRegistrationRecoveryOtp({
        phone: phone.trim(),
        otp: otp.trim(),
        registrationRecoveryAttemptToken: attemptToken,
      })
      const recovered = await completePatientRegistrationRecovery(
        capability.registration_recovery_token
      )
      await storePatientAuthSession(
        recovered.access_token,
        recovered.device_enrollment_token
      )

      try {
        await ensureCurrentDeviceEnrollment()
        Keyboard.dismiss()
        router.replace('/patient/access-history')
      } catch (deviceError) {
        if (deviceError instanceof CurrentDeviceError && deviceError.code === 'RECOVERY_REQUIRED') {
          router.replace('/patient/recovery')
          return
        }
        throw deviceError
      }
    } catch (requestError) {
      if (requestError instanceof RegistrationRecoveryClientError) {
        if (
          requestError.kind === 'expired_attempt' ||
          requestError.kind === 'state_changed'
        ) {
          setAttemptToken(null)
          setOtp('')
          setStep('phone')
        } else if (requestError.kind === 'not_required') {
          setError(requestError.message)
          setStep('phone')
        } else {
          setError(requestError.message)
          setStep('otp')
        }
      } else {
        setError(
          requestError instanceof Error
            ? requestError.message
            : 'Unable to repair this account.'
        )
        setStep(attemptToken ? 'otp' : 'phone')
      }
    } finally {
      setBusy(false)
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
          <StatusBadge tone="warning">
            {step === 'phone'
              ? 'Account repair: identity check'
              : step === 'otp'
                ? 'Account repair: verify'
                : 'Account repair: reconciling'}
          </StatusBadge>
          <ScreenHeader
            title="Repair an existing account"
            description="Use this only when Nexa Care says an existing registration needs repair. A fresh OTP proves your identity; it does not restore old device authority."
          />

          <YStack gap="$4">
            <FormField
              id="registration-recovery-phone"
              label="Phone number"
              placeholder="Phone number"
              hint="Use the phone number connected to the existing account."
              value={phone}
              onChangeText={setPhone}
              keyboardType="phone-pad"
              autoComplete="tel"
              autoCapitalize="none"
              autoCorrect={false}
              disabled={busy || step !== 'phone'}
              returnKeyType="done"
              onSubmitEditing={sendOtp}
            />

            {step !== 'phone' ? (
              <FormField
                id="registration-recovery-otp"
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
                disabled={busy || step === 'repairing'}
                onSubmitEditing={repairAccount}
              />
            ) : null}

            {error !== null ? <InlineNotice title={error} tone="danger" /> : null}

            <ActionButton
              intent="primary"
              disabled={
                busy ||
                (step === 'phone' ? phone.trim().length < 10 : otp.trim().length !== 6)
              }
              aria-busy={busy}
              onPress={step === 'phone' ? sendOtp : repairAccount}
            >
              {busy
                ? step === 'phone'
                  ? 'Sending code...'
                  : 'Checking account...'
                : step === 'phone'
                  ? 'Send recovery OTP'
                  : 'Verify and repair account'}
            </ActionButton>

            {step === 'otp' ? (
              <ActionButton disabled={busy} onPress={restart}>
                Start over
              </ActionButton>
            ) : null}
            <ActionButton
              disabled={busy}
              onPress={() => router.replace('/patient/login')}
            >
              Back to sign in
            </ActionButton>
          </YStack>

          <Paragraph color="$nexaSecondary" fontSize={14} lineHeight={22}>
            Account repair never clears an identity revocation, reverses an erasure request, or
            restores a retired device key. Some historical states require manual review instead of
            automatic repair.
          </Paragraph>
        </AuthFrame>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
