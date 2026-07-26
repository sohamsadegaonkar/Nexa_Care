'use client'

import {
  Button,
  Card,
  H1,
  Input,
  Paragraph,
  ScrollView,
  Separator,
  Spinner,
  SwitchThemeButton,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { useRouter } from 'solito/navigation'
import { useRef, useState } from 'react'
import { KeyboardAvoidingView, Platform } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

import { ApiError, NexaApiClient, setAuthTokenProvider } from '../../utils/apiClient'

export function HomeScreen({ onNavigate }: { onNavigate?: (screen: string) => void }) {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const sessionTokenRef = useRef<string | null>(null)
  const [providerId, setProviderId] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [mfaToken, setMfaToken] = useState('')
  const [verifyLoading, setVerifyLoading] = useState(false)
  const [verifyError, setVerifyError] = useState<string | null>(null)

  const navigate = (screen: string) => {
    if (onNavigate) onNavigate(screen)
    else router.push(`/${screen}`)
  }

  const handleVerify = async () => {
    if (!mfaToken.trim() || !totpCode.trim()) {
      setVerifyError('Both the MFA token and the authenticator code are required.')
      return
    }

    const trimmedProviderId = providerId.trim()
    const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
    if (trimmedProviderId && !uuidPattern.test(trimmedProviderId)) {
      setVerifyError('Provider ID must be a valid UUID.')
      return
    }

    setVerifyLoading(true)
    setVerifyError(null)
    try {
      const data = await NexaApiClient.verifyMfa({
        mfa_token: mfaToken.trim(),
        totp_code: totpCode.trim(),
        ...(trimmedProviderId ? { provider_id: trimmedProviderId } : {}),
      })
      sessionTokenRef.current = data.access_token
      setAuthTokenProvider(() => sessionTokenRef.current)
      navigate('dashboard')
    } catch (error: unknown) {
      if (error instanceof ApiError) {
        if (error.status === 401) {
          setVerifyError('Invalid or expired MFA token/code. Please try again.')
        } else if (error.status === 403) {
          setVerifyError('This provider is not authorized to complete verification.')
        } else if (error.status === 429) {
          setVerifyError('Too many attempts. Please wait a minute before retrying.')
        } else {
          setVerifyError(error.message || 'Verification failed. Please try again.')
        }
      } else {
        setVerifyError('Unable to reach the authentication service.')
      }
    } finally {
      setVerifyLoading(false)
    }
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        flex={1}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={{
          flexGrow: 1,
          padding: 16,
          paddingBottom: insets.bottom + 32,
        }}
      >
        <YStack
          gap="$6"
          items="center"
        >
          <XStack
            width="100%"
            gap="$6"
            justify="center"
            flexWrap="wrap"
          >
            {Platform.OS === 'web' && <SwitchThemeButton />}
          </XStack>

          <YStack gap="$4">
            <H1
              text="center"
              color="$color12"
            >
              Welcome to Nexa Care.
            </H1>
            <Paragraph
              color="$color10"
              text="center"
            >
              Secure patient access and verified provider workflows.
            </Paragraph>
            <Separator />
          </YStack>

          <Card
            width="100%"
            maxW={320}
            p="$4"
            gap="$4"
            bg="$color2"
            borderWidth={1}
            borderColor="$borderColor"
          >
            <YStack gap="$2">
              <Text
                fontWeight="700"
                color="$color12"
              >
                Provider Verification
              </Text>
              <Text
                color="$color10"
                fontSize={13}
              >
                Enter the MFA token issued at login and your authenticator code.
              </Text>
            </YStack>

            <YStack gap="$3">
              <Input
                placeholder="Provider ID (optional)"
                value={providerId}
                onChangeText={setProviderId}
                autoCapitalize="none"
                size="$4"
              />
              <Input
                placeholder="MFA Token"
                value={mfaToken}
                onChangeText={setMfaToken}
                autoCapitalize="none"
                size="$4"
              />
              <Input
                placeholder="Authenticator Code (TOTP)"
                value={totpCode}
                onChangeText={setTotpCode}
                keyboardType="numeric"
                maxLength={8}
                size="$4"
              />
            </YStack>

            <Button
              theme="blue"
              disabled={verifyLoading}
              onPress={handleVerify}
            >
              {verifyLoading ? <Spinner color="$color12" /> : 'Verify & Authenticate'}
            </Button>

            {verifyError ? (
              <Text
                color="$red10"
                fontSize={13}
                text="center"
              >
                {verifyError}
              </Text>
            ) : null}
          </Card>

          <Button
            width="100%"
            maxW={320}
            theme="blue"
            onPress={() => router.push('/patient/login')}
          >
            Continue as Patient
          </Button>

          <Separator
            width="100%"
            maxW={320}
          />

          <YStack
            gap="$4"
            width="100%"
            maxW={320}
          >
            <Button
              theme="blue"
              onPress={() => navigate('scanner')}
            >
              NFC Scanner
            </Button>
            <Button
              theme="red"
              onPress={() => navigate('break-glass')}
            >
              Emergency Break-Glass
            </Button>
            <Button
              theme="green"
              onPress={() => navigate('dashboard')}
            >
              Dashboard
            </Button>
            <Button onPress={() => navigate('consent-history')}>Consent History</Button>
          </YStack>
        </YStack>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
