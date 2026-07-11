/**
 * Doctor login screen — email + password authentication with MFA support.
 *
 * Flow:
 *   1. User enters email + password
 *   2. Call login() from ProviderAuthContext
 *   3. If MFA required → show TOTP code input
 *   4. User enters TOTP code → call verifyMfa()
 *   5. On success → redirect to /doctor/dashboard
 *
 * Uses the shared NexaApiClient via ProviderAuthContext.
 * No hardcoded provider_id, no local URLs.
 *
 * Route: /doctor/login
 */

'use client'

import { YStack, H1, Paragraph, Input, Button, Text, Spinner, H4 } from '@my/ui'
import { useRouter } from 'next/navigation'
import { useState, useCallback } from 'react'
import { useProviderAuth } from './ProviderAuthContext'

type LoginStep = 'credentials' | 'mfa'

export function DoctorLoginScreen() {
  const router = useRouter()
  const { login, verifyMfa, loginError, loggingIn } = useProviderAuth()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [step, setStep] = useState<LoginStep>('credentials')
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const [mfaDetail, setMfaDetail] = useState<string | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)

  // ── Step 1: email + password ────────────────────────────────────────────

  const handleLogin = useCallback(async () => {
    setLocalError(null)
    if (!email.trim()) { setLocalError('Email is required.'); return }
    if (!password) { setLocalError('Password is required.'); return }

    try {
      const result = await login(email.trim(), password)
      if (result.type === 'mfa_required') {
        setMfaToken(result.mfaToken)
        setMfaDetail(result.detail)
        setStep('mfa')
      } else {
        // Direct success — redirect
        router.push('/doctor/dashboard')
      }
    } catch {
      // loginError is set by the context
    }
  }, [email, password, login, router])

  // ── Step 2: TOTP verification ───────────────────────────────────────────

  const handleVerifyMfa = useCallback(async () => {
    setLocalError(null)
    if (!totpCode.trim()) { setLocalError('Authenticator code is required.'); return }
    if (totpCode.trim().length < 6) { setLocalError('Code must be at least 6 digits.'); return }
    if (!mfaToken) { setLocalError('MFA session expired. Please start over.'); return }

    try {
      await verifyMfa(mfaToken, totpCode.trim())
      router.push('/doctor/dashboard')
    } catch {
      // loginError is set by the context
    }
  }, [totpCode, mfaToken, verifyMfa, router])

  const handleBackToCredentials = useCallback(() => {
    setStep('credentials')
    setMfaToken(null)
    setMfaDetail(null)
    setTotpCode('')
    setLocalError(null)
  }, [])

  const displayError = localError ?? loginError

  // ── Render: MFA step ────────────────────────────────────────────────────

  if (step === 'mfa') {
    return (
      <YStack flex={1} bg="$background" justifyContent="center" alignItems="center" padding="$6">
        <YStack width="100%" maxWidth={440} gap="$4">
          <YStack alignItems="center" gap="$2">
            <H4 color="$color12" fontSize={22}>Two-Factor Authentication</H4>
            <Paragraph color="$color10" fontSize={15} textAlign="center">
              {mfaDetail || 'Enter the code from your authenticator app.'}
            </Paragraph>
          </YStack>

          <YStack gap="$3">
            <YStack gap="$1">
              <Text color="$color11" fontSize={13} fontWeight="700">Authenticator Code</Text>
              <Input
                size="$4"
                placeholder="000000"
                value={totpCode}
                onChangeText={setTotpCode}
                maxLength={8}
                autoCapitalize="none"
                autoCorrect={false}
              />
            </YStack>

            {displayError && (
              <YStack backgroundColor="$red4" borderRadius="$3" padding="$3">
                <Text color="$red10" fontSize={14}>{displayError}</Text>
              </YStack>
            )}

            <Button
              theme="blue"
              size="$4"
              disabled={loggingIn || totpCode.trim().length < 6}
              onPress={handleVerifyMfa}
            >
              {loggingIn ? <Spinner color="$blue10" size="small" /> : 'Verify Code'}
            </Button>

            <Button
              size="$3"
              chromeless
              onPress={handleBackToCredentials}
            >
              Back to Sign In
            </Button>
          </YStack>
        </YStack>
      </YStack>
    )
  }

  // ── Render: Credentials step ────────────────────────────────────────────

  return (
    <YStack flex={1} bg="$background" justifyContent="center" alignItems="center" padding="$6">
      <YStack width="100%" maxWidth={440} gap="$4">
        <YStack alignItems="center" gap="$2">
          <H1 color="$color12" fontSize={36}>Nexa Care</H1>
          <Paragraph color="$color10" fontSize={18}>Provider Login</Paragraph>
        </YStack>

        <YStack gap="$3">
          <YStack gap="$1">
            <Text color="$color11" fontSize={13} fontWeight="700">Email</Text>
            <Input
              size="$4"
              placeholder="doctor@hospital.com"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              autoCorrect={false}
            />
          </YStack>

          <YStack gap="$1">
            <Text color="$color11" fontSize={13} fontWeight="700">Password</Text>
            <Input
              size="$4"
              placeholder="Enter password"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
            />
          </YStack>

          {displayError && (
            <YStack backgroundColor="$red4" borderRadius="$3" padding="$3">
              <Text color="$red10" fontSize={14}>{displayError}</Text>
            </YStack>
          )}

          <Button
            theme="blue"
            size="$4"
            disabled={loggingIn}
            onPress={handleLogin}
          >
            {loggingIn ? <Spinner color="$blue10" size="small" /> : 'Sign In'}
          </Button>
        </YStack>
      </YStack>
    </YStack>
  )
}
