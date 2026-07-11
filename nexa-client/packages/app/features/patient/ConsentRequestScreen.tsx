import { useRouter, useLocalSearchParams } from 'expo-router'
import { YStack, H2, Paragraph, Button, Text, ScrollView, XStack, Separator, Spinner } from 'tamagui'
import { useState, useEffect, useCallback } from 'react'
import { fetchChallenge, isChallengeExpired, type ConsentChallenge } from '../../services/consentSigning'
import { denyWithSignature } from '../../services/consentSigning'

/**
 * Consent request review screen.
 * Deep-link target: nexacare://patient/consent/:requestId
 *
 * Fetches the full challenge from the backend, displays provider
 * details, purpose, scope, and a countdown timer.  Two actions:
 * Approve (green) → biometric screen, Deny (red) → sign & submit.
 */

interface ConsentRequestScreenProps {
  /** Pre-fetched challenge (e.g. from push notification data) */
  initialChallenge?: ConsentChallenge
}

export default function ConsentRequestScreen({ initialChallenge }: ConsentRequestScreenProps) {
  const router = useRouter()
  const params = useLocalSearchParams<{ requestId?: string }>()
  const requestId = params.requestId ?? ''

  const [challenge, setChallenge] = useState<ConsentChallenge | null>(initialChallenge ?? null)
  const [loading, setLoading] = useState(!initialChallenge)
  const [error, setError] = useState<string | null>(null)
  const [denying, setDenying] = useState(false)
  const [expired, setExpired] = useState(false)
  const [countdown, setCountdown] = useState('')

  // Fetch challenge from API on mount
  useEffect(() => {
    if (initialChallenge || !requestId) return
    let cancelled = false

    async function load() {
      setLoading(true)
      setError(null)
      try {
        const data = await fetchChallenge(requestId)
        if (cancelled) return
        setChallenge(data)
        if (isChallengeExpired(data)) {
          setExpired(true)
        }
      } catch {
        if (!cancelled) {
          setError('This request may have expired or is not available.')
          setExpired(true)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [initialChallenge, requestId])

  // Countdown timer
  const updateCountdown = useCallback(() => {
    if (!challenge) return
    const diff = new Date(challenge.expires_at).getTime() - Date.now()
    if (diff <= 0) {
      setCountdown('Expired')
      setExpired(true)
      return
    }
    const m = Math.floor(diff / (1000 * 60))
    const s = Math.floor((diff % (1000 * 60)) / 1000)
    setCountdown(`${m}m ${s}s`)
  }, [challenge])

  useEffect(() => {
    updateCountdown()
    const timer = setInterval(updateCountdown, 1000)
    return () => clearInterval(timer)
  }, [updateCountdown])

  // Approve → navigate to biometric approval screen
  const handleApprove = () => {
    router.push({
      pathname: '/patient/biometric-approval',
      params: { requestId: challenge?.request_id ?? requestId },
    })
  }

  // Deny → sign denial and submit (no biometric required per WS2)
  const handleDeny = async () => {
    if (!challenge) return
    setDenying(true)
    setError(null)
    try {
      await denyWithSignature(challenge)
      router.replace({
        pathname: '/patient/approval-result',
        params: {
          requestId: challenge.request_id,
          decision: 'denied',
          providerName: challenge.provider_name,
        },
      })
    } catch {
      setError('Failed to deny request. Please try again.')
    } finally {
      setDenying(false)
    }
  }

  // ── Render: Loading ──────────────────────────────────────────────────
  if (loading && !challenge) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center">
        <Spinner size="large" color="$blue10" />
        <Paragraph col="$colorSubdued" size="$4" mt="$3">Loading consent request...</Paragraph>
      </YStack>
    )
  }

  // ── Render: Expired / Not Found ──────────────────────────────────────
  if (!challenge || expired) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$3" p="$4">
        <Text fontSize={44}>⏰</Text>
        <H2 col="$color" ta="center">Request Expired</H2>
        <Paragraph col="$colorSubdued" ta="center" size="$4">
          {error ?? 'This consent request has expired. No action is needed.'}
        </Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.replace('/patient/access-history')}>
          Go to Access History
        </Button>
      </YStack>
    )
  }

  // ── Render: Scope as list ────────────────────────────────────────────
  const scopeItems = typeof challenge.scope === 'string'
    ? challenge.scope.split(',').map((s) => s.trim()).filter(Boolean)
    : challenge.scope

  const accessMinutes = Math.ceil(challenge.access_duration / 60)

  // ── Render: Active challenge ─────────────────────────────────────────
  return (
    <YStack f={1} bg="$background">
      <ScrollView contentContainerStyle={{ padding: 16, gap: 16 }}>
        <YStack gap="$2" ai="center" mt="$2">
          <Text fontSize={44}>📋</Text>
          <H2 col="$color" ta="center">Access Request</H2>
        </YStack>

        <YStack bg="$backgroundHover" br="$4" p="$4" gap="$3">
          <YStack>
            <Paragraph col="$colorSubdued" size="$2" textTransform="uppercase" letterSpacing={1}>
              Requesting Provider
            </Paragraph>
            <Text col="$color" size="$5" fontWeight="600">{challenge.provider_name}</Text>
            <Text col="$colorSubdued" size="$3">{challenge.hospital_name}</Text>
          </YStack>

          <Separator />

          <YStack>
            <Paragraph col="$colorSubdued" size="$2" textTransform="uppercase" letterSpacing={1}>
              Purpose
            </Paragraph>
            <Text col="$color" size="$4">{challenge.purpose}</Text>
          </YStack>

          <Separator />

          <YStack>
            <Paragraph col="$colorSubdued" size="$2" textTransform="uppercase" letterSpacing={1}>
              Data Requested
            </Paragraph>
            <YStack gap="$1" mt="$1">
              {scopeItems.map((item) => (
                <XStack key={item} gap="$2" ai="center">
                  <Text size="$3">•</Text>
                  <Text col="$color" size="$3">{item}</Text>
                </XStack>
              ))}
            </YStack>
          </YStack>

          <Separator />

          <XStack jc="space-between" ai="center">
            <Paragraph col="$colorSubdued" size="$2" textTransform="uppercase" letterSpacing={1}>
              Access Duration
            </Paragraph>
            <Text col="$orange10" size="$4" fontWeight="600">{accessMinutes} minute{accessMinutes !== 1 ? 's' : ''}</Text>
          </XStack>

          <Separator />

          <XStack jc="space-between" ai="center">
            <Paragraph col="$colorSubdued" size="$2" textTransform="uppercase" letterSpacing={1}>
              Request Expires In
            </Paragraph>
            <Text col="$red10" size="$5" fontWeight="700" fontFamily="$mono">{countdown}</Text>
          </XStack>
        </YStack>

        <Paragraph col="$colorSubdued" ta="center" size="$3" mw={340} mx="auto">
          {challenge.provider_name} from {challenge.hospital_name} is requesting
          access to your medical record for {challenge.purpose}.
          Access duration: {accessMinutes} minute{accessMinutes !== 1 ? 's' : ''}.
          Data requested: {scopeItems.join(', ')}. Approve only if you recognize this request.
        </Paragraph>
      </ScrollView>

      <YStack p="$4" gap="$3" bg="$background">
        <Button
          size="$4"
          bg="$green9"
          color="white"
          fontWeight="700"
          disabled={expired || denying}
          onPress={handleApprove}
        >
          Approve
        </Button>
        <Button
          size="$4"
          bg="$red9"
          color="white"
          fontWeight="700"
          disabled={expired || denying}
          onPress={handleDeny}
        >
          {denying ? 'Denying...' : 'Deny'}
        </Button>
      </YStack>

      {error && (
        <Text col="$red10" ta="center" size="$3" px="$4">{error}</Text>
      )}
    </YStack>
  )
}
