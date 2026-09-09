'use client'

import {
  ActionButton,
  AuthFrame,
  FormField,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenHeader,
  Separator,
  StatusBadge,
  XStack,
  YStack,
} from '@my/ui'
import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useProviderAuth } from './ProviderAuthContext'

interface DoctorLoginScreenProps {
  showEntryOptions?: boolean
}

function safeReturnTo(value: string | null): string {
  return value?.startsWith('/') && !value.startsWith('//') ? value : '/doctor/dashboard'
}

export function DoctorLoginScreen({ showEntryOptions = false }: DoctorLoginScreenProps) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { status, hydrated, login, verifyMfa, cancelMfa, loginError, loggingIn } = useProviderAuth()
  const demoMode = process.env.NEXT_PUBLIC_DEMO_MODE === 'true'
  const [email, setEmail] = useState(demoMode ? 'demo.doctor@nexacare.in' : '')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const submittingRef = useRef(false)
  const returnTo = safeReturnTo(searchParams.get('returnTo'))

  useEffect(() => {
    if (hydrated && status === 'authenticated') router.replace(returnTo)
  }, [hydrated, returnTo, router, status])

  const handleLogin = useCallback(async () => {
    if (submittingRef.current) return
    setLocalError(null)
    if (!email.trim()) {
      setLocalError('Email or login identifier is required.')
      return
    }
    if (!password) {
      setLocalError('Password is required.')
      return
    }
    submittingRef.current = true
    try {
      const result = await login(email, password)
      setPassword('')
      if (result.type === 'authenticated') router.replace(returnTo)
    } catch {
      // ProviderAuthContext maps the error without exposing submitted values.
    } finally {
      submittingRef.current = false
    }
  }, [email, login, password, returnTo, router])

  const handleVerifyMfa = useCallback(async () => {
    if (submittingRef.current) return
    setLocalError(null)
    if (!/^\d{6,8}$/.test(totpCode.trim())) {
      setLocalError('Enter a valid authenticator code.')
      return
    }
    submittingRef.current = true
    try {
      await verifyMfa(totpCode)
      setTotpCode('')
      router.replace(returnTo)
    } catch {
      // Invalid code remains on MFA; expired MFA state returns to credentials.
    } finally {
      submittingRef.current = false
    }
  }, [returnTo, router, totpCode, verifyMfa])

  const backToLogin = useCallback(() => {
    setTotpCode('')
    setLocalError(null)
    cancelMfa()
  }, [cancelMfa])

  if (!hydrated)
    return (
      <AuthFrame>
        <LoadingState label="Preparing sign in…" />
      </AuthFrame>
    )

  const displayError = localError ?? loginError
  const mfa = status === 'mfa_required'

  return (
    <AuthFrame>
      <StatusBadge tone="info">
        {mfa ? 'Step 2 · Verify your sign in' : 'Provider workspace'}
      </StatusBadge>
      <ScreenHeader
        title={mfa ? 'Verify Provider' : 'Provider Login'}
        description={
          mfa
            ? 'Enter the current code from your authenticator app.'
            : 'Welcome back. Sign in to continue caring for your patients.'
        }
      />
      {demoMode && (
        <InlineNotice
          title="Demo mode — credentials are supplied separately"
          tone="warning"
        />
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void (mfa ? handleVerifyMfa() : handleLogin())
        }}
      >
        <YStack gap="$4">
          {mfa ? (
            <FormField
              key="mfa"
              id="provider-code"
              label="Authenticator Code"
              placeholder="000000"
              value={totpCode}
              onChangeText={setTotpCode}
              keyboardType="numeric"
              inputMode="numeric"
              maxLength={8}
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="one-time-code"
              autoFocus
              letterSpacing={6}
              fontSize={24}
              textAlign="center"
              readOnly={loggingIn}
              onSubmitEditing={handleVerifyMfa}
            />
          ) : (
            <>
              <FormField
                id="provider-identifier"
                label="Email or Login Identifier"
                placeholder="doctor@hospital.com"
                value={email}
                onChangeText={setEmail}
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="username"
                readOnly={loggingIn}
                onSubmitEditing={handleLogin}
              />
              <FormField
                id="provider-password"
                label="Password"
                placeholder="Enter password"
                value={password}
                onChangeText={setPassword}
                secureTextEntry
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="current-password"
                readOnly={loggingIn}
                onSubmitEditing={handleLogin}
              />
            </>
          )}
          {displayError && (
            <InlineNotice
              title={displayError}
              tone="danger"
            />
          )}
          <ActionButton
            intent="primary"
            disabled={loggingIn || (mfa && totpCode.trim().length < 6)}
            onPress={mfa ? handleVerifyMfa : handleLogin}
            aria-busy={loggingIn}
          >
            {loggingIn ? (mfa ? 'Verifying…' : 'Signing in…') : mfa ? 'Verify' : 'Sign In'}
          </ActionButton>
          {loggingIn && (
            <Paragraph
              aria-live="polite"
              color="$nexaSecondary"
              fontSize={13}
            >
              Please wait while we verify your sign in.
            </Paragraph>
          )}
        </YStack>
      </form>
      {mfa ? (
        <ActionButton
          disabled={loggingIn}
          onPress={backToLogin}
        >
          Back to Sign In
        </ActionButton>
      ) : (
        <Paragraph
          color="$nexaSecondary"
          fontSize={13}
          lineHeight={21}
        >
          Use the account associated with your care organization.
        </Paragraph>
      )}
      {showEntryOptions && !mfa && (
        <>
          <Separator borderColor="$nexaBorder" />
          <XStack
            flexWrap="wrap"
            gap="$2"
          >
            <ActionButton
              flex={1}
              minWidth={180}
              onPress={() => router.push('/patient/login')}
            >
              Continue as Patient
            </ActionButton>
            <ActionButton
              flex={1}
              minWidth={140}
              onPress={() => router.push('/scanner')}
            >
              NFC Scanner
            </ActionButton>
          </XStack>
        </>
      )}
    </AuthFrame>
  )
}
