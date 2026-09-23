import { useRouter } from 'expo-router'
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
import { useState } from 'react'
import { Keyboard, KeyboardAvoidingView, Platform, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { storeProviderAuthSession } from '../../services/providerAuthSession'

export interface ProviderLoginScreenProps {
  onSuccess?: () => void
  returnTo?: string
}

export function ProviderLoginScreen({ onSuccess, returnTo = '/dashboard' }: ProviderLoginScreenProps = {}) {
  const router = useRouter()
  const insets = useSafeAreaInsets()

  const [step, setStep] = useState<'credentials' | 'mfa'>('credentials')
  const [loginIdentifier, setLoginIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleLogin = async () => {
    if (!loginIdentifier.trim() || !password) {
      setError('Email or login identifier and password are required.')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const result = await NexaApiClient.providerLogin({
        login_identifier: loginIdentifier.trim(),
        password,
      })

      if ('mfa_token' in result && result.mfa_token) {
        setMfaToken(result.mfa_token)
        setPassword('')
        setStep('mfa')
        return
      }

      if ('access_token' in result && result.access_token) {
        await storeProviderAuthSession(result.access_token, {
          providerUid: result.provider_uid,
          hospitalId: String(result.hospital_id),
          expiresAt: result.expires_at,
        })
        Keyboard.dismiss()
        if (onSuccess) {
          onSuccess()
        } else {
          router.replace(returnTo as any)
        }
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError('Invalid login identifier or password.')
        } else if (err.status === 403) {
          setError('This provider account is not authorized to sign in.')
        } else if (err.status === 429) {
          setError('Too many login attempts. Please wait a moment before trying again.')
        } else {
          setError(err.message || 'Login failed. Please try again.')
        }
      } else {
        setError('Unable to reach the authentication service.')
      }
    } finally {
      setLoading(false)
    }
  };

  const handleVerifyMfa = async () => {
    if (!mfaToken) {
      setError('MFA session expired. Please sign in again.')
      setStep('credentials')
      return
    }

    if (totpCode.trim().length < 6) {
      setError('Enter a valid 6-digit authenticator code.')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const result = await NexaApiClient.providerMfaVerify({
        mfa_token: mfaToken,
        totp_code: totpCode.trim(),
      })

      await storeProviderAuthSession(result.access_token, {
        providerUid: result.provider_uid,
        hospitalId: String(result.hospital_id),
        expiresAt: result.expires_at,
      })

      setMfaToken(null)
      Keyboard.dismiss()
      if (onSuccess) {
        onSuccess()
      } else {
        router.replace(returnTo as any)
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError('Invalid authenticator code. Please try again.')
        } else if (err.status === 410) {
          setError('MFA session has expired. Please sign in again.')
          setMfaToken(null)
          setStep('credentials')
        } else {
          setError(err.message || 'Verification failed. Please try again.')
        }
      } else {
        setError('Unable to complete MFA verification.')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleBackToCredentials = () => {
    setMfaToken(null)
    setTotpCode('')
    setError(null)
    setStep('credentials')
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{
          flexGrow: 1,
          paddingTop: insets.top + 24,
          paddingBottom: insets.bottom + 24,
        }}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
        showsVerticalScrollIndicator={false}
      >
        <AuthFrame>
          <StatusBadge tone="info">
            {step === 'credentials'
              ? 'Provider Workspace'
              : 'Step 2 of 2: Authenticator'}
          </StatusBadge>

          <ScreenHeader
            title={step === 'credentials' ? 'Provider Login' : 'Authenticator Code'}
            description={
              step === 'credentials'
                ? 'Welcome back. Sign in to access your clinical workspace.'
                : 'Enter the current 6-digit code from your authenticator app.'
            }
          />

          <YStack gap="$4">
            {step === 'credentials' ? (
              <>
                <FormField
                  id="provider-identifier"
                  label="Email or Login Identifier"
                  placeholder="doctor@hospital.com"
                  value={loginIdentifier}
                  onChangeText={setLoginIdentifier}
                  autoCapitalize="none"
                  autoCorrect={false}
                  autoComplete="username"
                  disabled={loading}
                  returnKeyType="next"
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
                  disabled={loading}
                  returnKeyType="done"
                  onSubmitEditing={handleLogin}
                />
              </>
            ) : (
              <FormField
                key="totp-code"
                id="provider-totp"
                label="Authenticator Code"
                placeholder="000000"
                value={totpCode}
                onChangeText={setTotpCode}
                keyboardType="number-pad"
                inputMode="numeric"
                maxLength={8}
                letterSpacing={6}
                fontSize={24}
                textAlign="center"
                autoFocus
                disabled={loading}
                returnKeyType="done"
                onSubmitEditing={handleVerifyMfa}
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
              disabled={
                loading ||
                (step === 'credentials'
                  ? !loginIdentifier.trim() || !password
                  : totpCode.trim().length < 6)
              }
              aria-busy={loading}
              onPress={step === 'credentials' ? handleLogin : handleVerifyMfa}
            >
              {loading
                ? step === 'credentials'
                  ? 'Signing in...'
                  : 'Verifying...'
                : step === 'credentials'
                  ? 'Sign In'
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

            {step === 'mfa' && (
              <ActionButton
                disabled={loading}
                onPress={handleBackToCredentials}
              >
                Back to Sign In
              </ActionButton>
            )}
          </YStack>

          <Paragraph
            color="$nexaSecondary"
            fontSize={13}
            lineHeight={20}
            textAlign="center"
          >
            Access is logged and audited according to clinical governance standards.
          </Paragraph>
        </AuthFrame>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
