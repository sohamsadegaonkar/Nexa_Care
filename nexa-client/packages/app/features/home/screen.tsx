'use client'

import {
  Anchor,
  Button,
  Card,
  H1,
  Input,
  Paragraph,
  Separator,
  Sheet,
  Spinner,
  SwitchThemeButton,
  Text,
  useToastController,
  XStack,
  YStack,
} from '@my/ui'
import { ChevronDown, ChevronUp } from '@tamagui/lucide-icons'
import axios from 'axios'
import { useRouter } from 'solito/navigation'
import { useRef, useState } from 'react'
import { Platform } from 'react-native'

import { apiClient, setAuthTokenProvider } from '../../utils/api'

interface ProviderMfaVerifyResponse {
  access_token: string
  token_type: string
  expires_at: string
  provider_uid: string
  hospital_id: string
}

export function HomeScreen({ onNavigate }: { onNavigate?: (screen: string) => void }) {
  const router = useRouter()

  // Holds the verified provider session token in memory. We register a
  // *closure* with setAuthTokenProvider (never the raw string) so the
  // axios interceptor in utils/api.ts always reads the current value.
  const sessionTokenRef = useRef<string | null>(null)

  // --- Lane A: Provider MFA verification state ---
  // The backend's ProviderMfaVerifyRequest requires `mfa_token` (the
  // pending token from POST /auth/login) + `totp_code` (the 6-8 digit
  // authenticator code). `provider_id` is also accepted, but ONLY as a
  // non-authoritative, client-echo integrity check — the server resolves
  // real identity exclusively from the Redis-backed mfa_token, never from
  // this field. If it's supplied and doesn't match, the server treats it
  // as a session-confusion/IDOR probe and rejects with 401.
  const [providerId, setProviderId] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [mfaToken, setMfaToken] = useState('')
  const [verifyLoading, setVerifyLoading] = useState(false)
  const [verifyError, setVerifyError] = useState<string | null>(null)

  const navigate = (screen: string) => {
    if (onNavigate) {
      onNavigate(screen)
    } else {
      router.push(`/${screen}`)
    }
  }

  const UUID_PATTERN =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

  const handleVerify = async () => {
    if (!mfaToken.trim() || !totpCode.trim()) {
      setVerifyError('Both the MFA token and the authenticator code are required.')
      return
    }

    const trimmedProviderId = providerId.trim()
    if (trimmedProviderId && !UUID_PATTERN.test(trimmedProviderId)) {
      setVerifyError('Provider ID must be a valid UUID.')
      return
    }

    setVerifyLoading(true)
    setVerifyError(null)

    try {
      const response = await apiClient.post<ProviderMfaVerifyResponse>(
        '/api/v2/auth/mfa/verify',
        {
          mfa_token: mfaToken.trim(),
          totp_code: totpCode.trim(),
          // Non-authoritative: server verifies this against the identity
          // bound to mfa_token and rejects on mismatch. Omitted entirely
          // if left blank, since it's optional server-side.
          ...(trimmedProviderId ? { provider_id: trimmedProviderId } : {}),
        }
      )

      const { access_token } = response.data

      // Persist the token, then wire it into the shared auth utility.
      // Nothing navigates until the token is actually captured — no
      // silent-failure path to /dashboard with an empty session.
      sessionTokenRef.current = access_token
      setAuthTokenProvider(() => sessionTokenRef.current)

      router.push('/dashboard')
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        const status = err.response?.status

        if (status === 401) {
          setVerifyError('Invalid or expired MFA token/code. Please try again.')
        } else if (status === 403) {
          setVerifyError('This provider is not authorized to complete verification.')
        } else if (status === 429) {
          setVerifyError('Too many attempts. Please wait a minute before retrying.')
        } else {
          const detail =
            typeof err.response?.data?.detail === 'string'
              ? err.response.data.detail
              : 'Verification failed. Please try again.'
          setVerifyError(detail)
        }
      } else {
        setVerifyError('Unable to reach the authentication service.')
      }
    } finally {
      setVerifyLoading(false)
    }
  }

  return (
    <YStack
      flex={1}
      justify="center"
      items="center"
      gap="$8"
      p="$4"
      bg="$background"
    >
      <XStack
        position="absolute"
        width="100%"
        t="$6"
        gap="$6"
        justify="center"
        flexWrap="wrap"
        $sm={{ position: 'relative', t: 0 }}
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
          Provider-facing scanner and emergency break-glass access.
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
          <Text fontWeight="700" color="$color12">
            Provider Verification
          </Text>
          <Text color="$color10" fontSize={13}>
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

        {verifyError && (
          <Text color="$red10" fontSize={13} text="center">
            {verifyError}
          </Text>
        )}
      </Card>

      <YStack gap="$4" width="100%" maxW={320}>
        <Button theme="blue" onPress={() => router.push('/scanner')}>
          NFC Scanner
        </Button>
        <Button theme="red" onPress={() => router.push('/break-glass')}>
          Emergency Break-Glass
        </Button>
        <Button theme="green" onPress={() => router.push('/dashboard')}>
          Dashboard
        </Button>
        <Button onPress={() => router.push('/consent-history')}>
          Consent History
        </Button>
      </YStack>

      <SheetDemo />
    </YStack>
  )
}

function SheetDemo() {
  const toast = useToastController()

  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState(0)

  return (
    <>
      <Button
        size="$6"
        icon={open ? ChevronDown : ChevronUp}
        circular
        onPress={() => setOpen((x) => !x)}
      />
      <Sheet
        modal
        transition="medium"
        open={open}
        onOpenChange={setOpen}
        snapPoints={[80]}
        position={position}
        onPositionChange={setPosition}
        dismissOnSnapToBottom
      >
        <Sheet.Overlay
          bg="$shadow4"
          transition="lazy"
          enterStyle={{ opacity: 0 }}
          exitStyle={{ opacity: 0 }}
        />
        <Sheet.Handle bg="$color8" />
        <Sheet.Frame
          items="center"
          justify="center"
          gap="$10"
          bg="$color2"
        >
          <XStack gap="$2">
            <Paragraph text="center">Made by</Paragraph>
            <Anchor
              color="$blue10"
              href="https://twitter.com/natebirdman"
              target="_blank"
            >
              @natebirdman,
            </Anchor>
            <Anchor
              color="$blue10"
              href="https://github.com/tamagui/tamagui"
              target="_blank"
              rel="noreferrer"
            >
              give it a ⭐️
            </Anchor>
          </XStack>

          <Button
            size="$6"
            circular
            icon={ChevronDown}
            onPress={() => {
              setOpen(false)
              toast.show('Sheet closed!', {
                message: 'Just showing how toast works...',
              })
            }}
          />
        </Sheet.Frame>
      </Sheet>
    </>
  )
}