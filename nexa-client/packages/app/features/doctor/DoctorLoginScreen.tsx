'use client'

import {
  Button,
  H1,
  H4,
  Input,
  Paragraph,
  Separator,
  Spinner,
  Text,
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
  const {
    status,
    hydrated,
    login,
    verifyMfa,
    cancelMfa,
    loginError,
    loggingIn,
  } = useProviderAuth()
  const [email, setEmail] = useState('demo.doctor@nexacare.in')
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
    if (!email.trim()) { setLocalError('Email or login identifier is required.'); return }
    if (!password) { setLocalError('Password is required.'); return }
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

  if (!hydrated) {
    return (
      <YStack flex={1} bg="$background" justifyContent="center" alignItems="center">
        <Spinner size="large" color="$blue10" />
      </YStack>
    )
  }

  const displayError = localError ?? loginError

  if (status === 'mfa_required') {
    return (
      <YStack flex={1} bg="$background" justifyContent="center" alignItems="center" padding="$6">
        <YStack width="100%" maxWidth={440} gap="$4">
          <YStack alignItems="center" gap="$2">
            <H4 color="$color12" fontSize={22}>Verify Provider</H4>
            <Paragraph color="$color10" fontSize={15} textAlign="center">
              Enter the current code from your authenticator app.
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
                keyboardType="numeric"
                maxLength={8}
                autoCapitalize="none"
                autoCorrect={false}
              />
            </YStack>
            {displayError ? (
              <YStack backgroundColor="$red4" borderRadius="$3" padding="$3">
                <Text color="$red10" fontSize={14}>{displayError}</Text>
              </YStack>
            ) : null}
            <Button
              theme="blue"
              size="$4"
              disabled={loggingIn || totpCode.trim().length < 6}
              onPress={handleVerifyMfa}
            >
              {loggingIn ? <Spinner color="$blue10" size="small" /> : 'Verify'}
            </Button>
            <Button size="$3" chromeless onPress={backToLogin}>
              Back to Provider Login
            </Button>
          </YStack>
        </YStack>
      </YStack>
    )
  }

  return (
    <YStack flex={1} bg="$background" justifyContent="center" alignItems="center" padding="$6">
      <YStack width="100%" maxWidth={440} gap="$4">
        <YStack alignItems="center" gap="$2">
          <H1 color="$color12" fontSize={36}>Nexa Care</H1>
          <Paragraph color="$color10" fontSize={18}>Provider Login</Paragraph>
        </YStack>
        <YStack gap="$3">
          <YStack gap="$1">
            <Text color="$color11" fontSize={13} fontWeight="700">
              Email or Login Identifier
            </Text>
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
          {displayError ? (
            <YStack backgroundColor="$red4" borderRadius="$3" padding="$3">
              <Text color="$red10" fontSize={14}>{displayError}</Text>
            </YStack>
          ) : null}
          <Button theme="blue" size="$4" disabled={loggingIn} onPress={handleLogin}>
            {loggingIn ? <Spinner color="$blue10" size="small" /> : 'Sign In'}
          </Button>
        </YStack>

        {showEntryOptions ? (
          <>
            <Separator />
            <YStack gap="$3">
              <Button onPress={() => router.push('/patient/login')}>Continue as Patient</Button>
              <Button onPress={() => router.push('/scanner')}>NFC Scanner</Button>
            </YStack>
          </>
        ) : null}
      </YStack>
    </YStack>
  )
}
