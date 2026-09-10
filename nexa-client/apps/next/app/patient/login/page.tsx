'use client'

import {
  ActionButton,
  AuthFrame,
  FormField,
  InlineNotice,
  Paragraph,
  ScreenHeader,
  Separator,
  StatusBadge,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { ShieldCheck, Phone, KeyRound, ArrowRight } from '@tamagui/lucide-icons'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { requestPatientOtp, verifyPatientOtp } from 'app/services/patientOtp'
import { storePatientAuthSession } from 'app/services/patientAuthSession'

export default function PatientLoginPage() {
  const router = useRouter()
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState('')
  const [step, setStep] = useState<'phone' | 'otp'>('phone')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSendOtp = async () => {
    if (!phone.trim()) {
      setError('Please enter your phone number.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const normalizedPhone = await requestPatientOtp(phone)
      setPhone(normalizedPhone)
      setStep('otp')
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : 'Unable to send verification code. Please check your number.'
      )
    } finally {
      setLoading(false)
    }
  }

  const handleVerifyOtp = async () => {
    if (!otp.trim() || otp.trim().length < 6) {
      setError('Please enter the complete 6-digit verification code.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await verifyPatientOtp(phone, otp)
      await storePatientAuthSession(data.access_token, data.device_enrollment_token)
      router.push('/patient/dashboard')
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : 'Invalid verification code. Please try again.'
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthFrame>
      <StatusBadge tone="info">
        {step === 'phone' ? 'Patient Portal · Step 1 of 2' : 'Patient Portal · Step 2 of 2'}
      </StatusBadge>

      <ScreenHeader
        title={step === 'phone' ? 'Your Health, Your Control' : 'Enter Verification Code'}
        description={
          step === 'phone'
            ? 'Sign in with your phone to review doctor access, inspect your medical timeline, and manage consent.'
            : `We sent a 6-digit verification code to ${phone}.`
        }
      />

      <YStack gap="$4">
        {step === 'phone' ? (
          <FormField
            id="web-patient-phone"
            label="Mobile Number"
            placeholder="+91 98765 43210"
            hint="Include country code (e.g. +91 for India)."
            value={phone}
            onChangeText={(text) => {
              setPhone(text)
              if (error) setError(null)
            }}
            disabled={loading}
            onSubmitEditing={handleSendOtp}
          />
        ) : (
          <YStack gap="$3">
            <FormField
              id="web-patient-otp"
              label="6-Digit Verification Code"
              placeholder="000000"
              value={otp}
              onChangeText={(text) => {
                setOtp(text)
                if (error) setError(null)
              }}
              maxLength={6}
              disabled={loading}
              letterSpacing={6}
              fontSize={24}
              textAlign="center"
              onSubmitEditing={handleVerifyOtp}
            />
            <XStack justifyContent="space-between" alignItems="center">
              <ActionButton
                chromeless
                onPress={() => {
                  setStep('phone')
                  setOtp('')
                  setError(null)
                }}
              >
                Change Phone Number
              </ActionButton>
              <ActionButton
                chromeless
                onPress={handleSendOtp}
                disabled={loading}
              >
                Resend Code
              </ActionButton>
            </XStack>
          </YStack>
        )}

        {error && (
          <InlineNotice title={error} tone="danger" />
        )}

        <ActionButton
          intent="primary"
          disabled={loading || (step === 'phone' ? !phone.trim() : otp.trim().length < 6)}
          aria-busy={loading}
          onPress={step === 'phone' ? handleSendOtp : handleVerifyOtp}
        >
          {loading ? (
            'Verifying…'
          ) : step === 'phone' ? (
            <XStack alignItems="center" gap="$2">
              <Text color="$nexaOnAccent" fontWeight="700">Send Verification Code</Text>
              <ArrowRight size={16} color="$nexaOnAccent" />
            </XStack>
          ) : (
            'Verify & Open Dashboard'
          )}
        </ActionButton>

        <Separator borderColor="$nexaBorder" />

        {/* Security / Privacy Trust Callout */}
        <XStack alignItems="center" gap="$2" justifyContent="center">
          <ShieldCheck size={16} color="$nexaAccent" />
          <Text color="$nexaSecondary" fontSize={12} fontWeight="600">
            Direct patient authentication with scoped consent and audit logging
          </Text>
        </XStack>

        <ActionButton
          chromeless
          onPress={() => router.push('/doctor/login')}
        >
          Are you a clinician? Go to Provider Sign In
        </ActionButton>
      </YStack>
    </AuthFrame>
  )
}
