import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'solito/navigation'
import {
  ActionButton,
  FormField,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenContainer,
  ScreenHeader,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { Lock, Phone, ShieldCheck } from '@tamagui/lucide-icons'
import { requestPatientOtp } from '../../services/patientOtp'
import {
  disablePhoneDiscoverability,
  enablePhoneDiscoverability,
  getPhoneDiscoverability,
} from '../../services/patientPhoneDiscoverability'
import { ApiError } from '../../utils/apiClient'

type Step = 'phone' | 'otp'

function safeError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Unable to update phone discoverability. Please try again.'
  if (error.code === 'PHONE_DISCOVERABILITY_RATE_LIMITED' || error.status === 429) {
    return 'Too many verification attempts. Wait before trying again.'
  }
  if (error.status === 401) return 'The verification code is invalid or expired. Request a new code.'
  if (error.status === 409) {
    return 'Phone discoverability could not be enabled for this account. Contact support if this continues.'
  }
  if (error.status >= 500 || error.status === 0) {
    return 'Phone discoverability is temporarily unavailable. Please try again later.'
  }
  return 'Unable to update phone discoverability. Please try again.'
}

export function PhoneDiscoverabilityScreen() {
  const router = useRouter()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [pendingPhone, setPendingPhone] = useState('')
  const [otp, setOtp] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const state = await getPhoneDiscoverability()
      setEnabled(state.enabled)
    } catch (caught) {
      setEnabled(null)
      setError(safeError(caught))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const sendOtp = async () => {
    if (busy || !phone.trim()) return
    setBusy(true)
    setError(null)
    try {
      const normalized = await requestPatientOtp(phone)
      setPendingPhone(normalized)
      setPhone('')
      setOtp('')
      setStep('otp')
    } catch (caught) {
      setError(safeError(caught))
    } finally {
      setBusy(false)
    }
  }

  const confirmEnable = async () => {
    if (busy || !pendingPhone || otp.length !== 6) return
    setBusy(true)
    setError(null)
    try {
      const state = await enablePhoneDiscoverability(pendingPhone, otp)
      setEnabled(state.enabled)
      setPendingPhone('')
      setOtp('')
      setStep('phone')
    } catch (caught) {
      setOtp('')
      setError(safeError(caught))
    } finally {
      setBusy(false)
    }
  }

  const disable = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const state = await disablePhoneDiscoverability()
      setEnabled(state.enabled)
      setPhone('')
      setPendingPhone('')
      setOtp('')
      setStep('phone')
    } catch (caught) {
      setError(safeError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="PRIVACY CONTROL"
        title="Phone Discoverability"
        description="Choose whether an authorized clinician can resolve your Nexa account using your verified phone number."
      />

      <Surface padding="$5" gap="$4">
        <XStack justifyContent="space-between" alignItems="center" gap="$3" flexWrap="wrap">
          <YStack gap="$1" flex={1} minWidth={240}>
            <Text fontSize={17} fontWeight="800">Exact phone lookup</Text>
            <Paragraph color="$nexaSecondary" fontSize={13}>
              This is off by default. Enabling it creates only a keyed exact-match search index; Nexa does not store your phone in the discovery index.
            </Paragraph>
          </YStack>
          {enabled === null ? (
            <StatusBadge tone="neutral">Status unavailable</StatusBadge>
          ) : enabled ? (
            <StatusBadge tone="success">Enabled</StatusBadge>
          ) : (
            <StatusBadge tone="neutral">Disabled</StatusBadge>
          )}
        </XStack>

        {error && <InlineNotice title={error} tone="danger" />}

        {enabled === true ? (
          <YStack gap="$3">
            <InlineNotice
              title="Clinicians with patient-discovery authority and recent MFA can perform exact phone lookup. They still receive only a short-lived discovery handle and must request your consent before clinical access."
              tone="info"
            />
            <ActionButton disabled={busy} onPress={disable}>
              {busy ? <LoadingState label="Disabling…" /> : 'Disable phone discoverability'}
            </ActionButton>
          </YStack>
        ) : step === 'phone' ? (
          <YStack gap="$3">
            <FormField
              id="discoverability-phone"
              label="Verified mobile number"
              placeholder="+91..."
              value={phone}
              onChangeText={(value) => {
                setPhone(value)
                if (error) setError(null)
              }}
              autoCapitalize="none"
              autoCorrect={false}
              disabled={busy}
              onSubmitEditing={sendOtp}
            />
            <ActionButton intent="primary" disabled={busy || !phone.trim()} onPress={sendOtp}>
              {busy ? <LoadingState label="Sending code…" /> : 'Send verification code'}
            </ActionButton>
          </YStack>
        ) : (
          <YStack gap="$3">
            <InlineNotice
              title="Enter the six-digit code sent to the phone you just verified. The phone and code remain only in this screen's memory and are cleared after submission."
              tone="info"
            />
            <FormField
              id="discoverability-otp"
              label="Verification code"
              placeholder="123456"
              value={otp}
              onChangeText={(value) => {
                setOtp(value.replace(/\D/g, '').slice(0, 6))
                if (error) setError(null)
              }}
              autoCapitalize="none"
              autoCorrect={false}
              disabled={busy}
              onSubmitEditing={confirmEnable}
            />
            <XStack gap="$3" flexWrap="wrap">
              <ActionButton
                disabled={busy}
                onPress={() => {
                  setPendingPhone('')
                  setOtp('')
                  setStep('phone')
                  setError(null)
                }}
              >
                Cancel
              </ActionButton>
              <ActionButton intent="primary" disabled={busy || otp.length !== 6} onPress={confirmEnable}>
                {busy ? <LoadingState label="Enabling…" /> : 'Enable exact phone lookup'}
              </ActionButton>
            </XStack>
          </YStack>
        )}
      </Surface>

      <Surface backgroundColor="$nexaMuted" borderColor="$nexaBorder" padding="$4">
        <YStack gap="$3">
          <XStack gap="$3" alignItems="flex-start">
            <Phone size={20} color="$nexaAccent" />
            <Paragraph color="$nexaSecondary" flex={1}>
              A phone match is only identification. It is not login authority, device trust, consent, or permission to read your medical record.
            </Paragraph>
          </XStack>
          <XStack gap="$3" alignItems="flex-start">
            <ShieldCheck size={20} color="$nexaAccent" />
            <Paragraph color="$nexaSecondary" flex={1}>
              No broad directory, fuzzy search, candidate list, or name-only lookup is enabled by this setting.
            </Paragraph>
          </XStack>
          <XStack gap="$3" alignItems="flex-start">
            <Lock size={20} color="$nexaAccent" />
            <Paragraph color="$nexaSecondary" flex={1}>
              You can disable this search binding at any time without changing your ability to sign in with your phone.
            </Paragraph>
          </XStack>
        </YStack>
      </Surface>

      <ActionButton onPress={() => router.back()}>Back</ActionButton>
    </ScreenContainer>
  )
}

export default PhoneDiscoverabilityScreen
