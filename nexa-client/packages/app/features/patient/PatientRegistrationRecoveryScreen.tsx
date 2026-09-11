import { useRouter } from 'solito/navigation'
import { Keyboard, KeyboardAvoidingView, Platform, ScrollView } from 'react-native'
import { useCallback, useEffect, useState } from 'react'
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
  RegistrationRecoveryReviewStatusResponse,
  completePatientRegistrationRecovery,
  getPatientRegistrationRecoveryReviewStatus,
  requestPatientRegistrationRecoveryOtp,
  verifyPatientRegistrationRecoveryOtp,
} from '../../services/patientRegistrationRecovery'

export type PatientRegistrationRecoveryStep =
  | 'phone'
  | 'otp'
  | 'repairing'
  | 'manual_review_waiting'
  | 'manual_review_terminal'

export default function PatientRegistrationRecoveryScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState('')
  const [attemptToken, setAttemptToken] = useState<string | null>(null)
  const [caseReference, setCaseReference] = useState<string | null>(null)
  const [reviewStatus, setReviewStatus] =
    useState<RegistrationRecoveryReviewStatusResponse | null>(null)
  const [step, setStep] = useState<PatientRegistrationRecoveryStep>('phone')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const restart = () => {
    setOtp('')
    setAttemptToken(null)
    setCaseReference(null)
    setReviewStatus(null)
    setError(null)
    setStep('phone')
  }

  const resetToStartWithError = (message: string) => {
    setAttemptToken(null)
    setCaseReference(null)
    setReviewStatus(null)
    setOtp('')
    setError(message)
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

  const checkReviewStatus = useCallback(
    async (isBackground = false) => {
      if (!caseReference) return
      if (!isBackground) setBusy(true)
      try {
        const latest = await getPatientRegistrationRecoveryReviewStatus(caseReference)
        setReviewStatus(latest)
        setError(null)
        if (latest.terminal) {
          setStep('manual_review_terminal')
        }
      } catch (statusError) {
        if (!isBackground) {
          setError(
            statusError instanceof Error
              ? statusError.message
              : 'Unable to refresh review status.'
          )
        }
      } finally {
        if (!isBackground) setBusy(false)
      }
    },
    [caseReference]
  )

  useEffect(() => {
    if (step !== 'manual_review_waiting' || !caseReference) return

    void checkReviewStatus(true)
    const timer = setInterval(() => {
      void checkReviewStatus(true)
    }, 5000)

    return () => clearInterval(timer)
  }, [step, caseReference, checkReviewStatus])

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
        if (
          deviceError instanceof CurrentDeviceError &&
          deviceError.code === 'RECOVERY_REQUIRED'
        ) {
          router.replace('/patient/recovery')
          return
        }
        throw deviceError
      }
    } catch (requestError) {
      if (requestError instanceof RegistrationRecoveryClientError) {
        if (requestError.kind === 'sign_in_required') {
          router.replace('/patient/login')
          return
        }
        if (requestError.kind === 'manual_review') {
          if (requestError.caseReference) {
            setCaseReference(requestError.caseReference)
            setAttemptToken(null)
            setOtp('')
            setError(null)
            setReviewStatus({
              case_reference: requestError.caseReference,
              status: 'PENDING',
              terminal: false,
              next_action: 'WAIT_FOR_REVIEW',
              created_at: new Date().toISOString(),
              resolved_at: null,
            })
            setStep('manual_review_waiting')
          } else {
            resetToStartWithError(requestError.message)
          }
          return
        }
        if (
          requestError.kind === 'expired_attempt' ||
          requestError.kind === 'state_changed' ||
          requestError.kind === 'not_available' ||
          requestError.kind === 'not_required'
        ) {
          resetToStartWithError(requestError.message)
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

  const renderContent = () => {
    if (step === 'manual_review_waiting') {
      const isEvaluating = reviewStatus?.status === 'IN_REVIEW'
      return (
        <>
          <StatusBadge tone="warning">
            {isEvaluating
              ? 'Account review: in review'
              : 'Account review: pending evaluation'}
          </StatusBadge>
          <ScreenHeader
            title="Account review in progress"
            description="Nexa Care is reviewing your account state. A separate review authority must evaluate the account before recovery can proceed."
          />

          <YStack gap="$4">
            <InlineNotice
              tone="warning"
              title={`Status: ${reviewStatus?.status ?? 'PENDING'}`}
              description="No session, tokens, or device authority have been granted. Review decisions do not automatically issue credentials or device authority."
            />

            <YStack
              padding="$4"
              borderRadius="$4"
              backgroundColor="$nexaSurface"
              borderColor="$nexaBorder"
              borderWidth={1}
              gap="$2"
            >
              <Paragraph color="$nexaSecondary" fontSize={13}>
                Case reference:
              </Paragraph>
              <Paragraph
                fontWeight="700"
                fontSize={16}
                color="$nexaText"
              >
                {caseReference}
              </Paragraph>
              <Paragraph color="$nexaSecondary" fontSize={12} lineHeight={18}>
                Retain this reference for hospital support inquiries. Status is
                periodically checked automatically.
              </Paragraph>
            </YStack>

            {error !== null ? <InlineNotice title={error} tone="danger" /> : null}

            <ActionButton
              intent="primary"
              disabled={busy}
              aria-busy={busy}
              onPress={() => void checkReviewStatus(false)}
            >
              {busy ? 'Checking status...' : 'Check status'}
            </ActionButton>

            <ActionButton
              disabled={busy}
              onPress={() => router.replace('/patient/login')}
            >
              Return to sign in
            </ActionButton>
          </YStack>
        </>
      )
    }

    if (step === 'manual_review_terminal') {
      const isResolved =
        reviewStatus?.status === 'RESOLVED' &&
        reviewStatus?.next_action === 'RESTART_ACCOUNT_RECOVERY'

      if (isResolved) {
        return (
          <>
            <StatusBadge tone="success">Account review: resolved</StatusBadge>
            <ScreenHeader
              title="Account review resolved"
              description="An authorized reviewer has resolved the registration state. You can now restart recovery using your phone number to authenticate fresh."
            />

            <YStack gap="$4">
              <InlineNotice
                tone="success"
                title="Review complete"
                description="To ensure security, no session was created automatically. Please restart account recovery to verify your identity."
              />

              <ActionButton intent="primary" onPress={restart}>
                Restart account recovery
              </ActionButton>

              <ActionButton onPress={() => router.replace('/patient/login')}>
                Back to sign in
              </ActionButton>
            </YStack>
          </>
        )
      }

      const isEscalated = reviewStatus?.status === 'SECURITY_ESCALATED'
      return (
        <>
          <StatusBadge tone="danger">
            {isEscalated ? 'Security escalation' : 'Recovery not available'}
          </StatusBadge>
          <ScreenHeader
            title={
              isEscalated
                ? 'Account security escalation'
                : 'Account recovery rejected'
            }
            description="This registration review has concluded and cannot be recovered self-service. Please contact hospital support or administration."
          />

          <YStack gap="$4">
            <InlineNotice
              tone="danger"
              title="Contact hospital support"
              description="No account repair was performed. Automated self-service recovery is disabled for this record."
            />

            <ActionButton onPress={() => router.replace('/patient/login')}>
              Back to sign in
            </ActionButton>
          </YStack>
        </>
      )
    }

    // Default flow: phone | otp | repairing
    return (
      <>
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
              (step === 'phone'
                ? phone.trim().length < 10
                : otp.trim().length !== 6)
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
          Account repair never clears an identity revocation, reverses an
          erasure request, or restores a retired device key. Some historical
          states require manual review instead of automatic repair.
        </Paragraph>
      </>
    )
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
          {renderContent()}
        </AuthFrame>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}

