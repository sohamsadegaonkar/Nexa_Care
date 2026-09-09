import { useRouter, useLocalSearchParams } from 'expo-router'
import {
  ActionButton,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenContainer,
  ScreenHeader,
  SectionHeading,
  StatusBadge,
  Surface,
  Text,
  ScrollView,
  XStack,
  YStack,
} from '@my/ui'
import { Clock, UserCheck } from '@tamagui/lucide-icons'
import { useState, useEffect, useCallback } from 'react'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import {
  classifyConsentError,
  fetchChallenge,
  isChallengeExpired,
  type ConsentChallenge,
} from '../../services/consentSigning'
import { denyWithSignature } from '../../services/consentSigning'
import { useResetToPatientAccessHistory } from '../../hooks/useResetToPatientAccessHistory'

/**
 * Consent request review screen.
 * Deep-link target: nexacare://patient/consent-request?requestId=:requestId
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
  const insets = useSafeAreaInsets()
  const resetToAccessHistory = useResetToPatientAccessHistory()
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
        if (data.status !== 'pending') {
          resetToAccessHistory()
          return
        }
        setChallenge(data)
        if (isChallengeExpired(data)) {
          setExpired(true)
        }
      } catch (loadError) {
        if (!cancelled) {
          const mapped = classifyConsentError(loadError)
          setError(mapped.message)
          if (mapped.kind === 'reauth') {
            setExpired(false)
            router.replace('/patient/login')
          } else {
            setExpired(mapped.kind === 'expired')
          }
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [initialChallenge, requestId, resetToAccessHistory, router])

  useEffect(() => {
    if (!challenge) return
    if (challenge.status !== 'pending' || expired) {
      resetToAccessHistory()
    }
  }, [challenge, expired, resetToAccessHistory])

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
    if (!challenge || challenge.status !== 'pending' || expired) return
    router.push({
      pathname: '/patient/biometric-approval',
      params: { requestId: challenge.request_id },
    })
  }

  // Deny → sign denial and submit (no biometric required per WS2)
  const handleDeny = async () => {
    if (!challenge || challenge.status !== 'pending' || expired) return
    setDenying(true)
    setError(null)
    try {
      await denyWithSignature(challenge)
      resetToAccessHistory()
    } catch {
      setError('Failed to deny request. Please try again.')
    } finally {
      setDenying(false)
    }
  }

  // ── Render: Loading ──────────────────────────────────────────────────
  if (loading && !challenge) return <LoadingState label="Loading consent request..." />

  if (!challenge || expired)
    return (
      <ScreenContainer>
        <ScreenHeader title={expired ? 'Request Expired' : 'Request Unavailable'} />
        <InlineNotice
          title={error ?? 'This request is no longer available. No access was approved here.'}
          tone="warning"
        />
        <ActionButton onPress={resetToAccessHistory}>Go to Access History</ActionButton>
      </ScreenContainer>
    )

  const scopeItems =
    typeof challenge.scope === 'string'
      ? challenge.scope
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)
      : challenge.scope
  const accessMinutes = Math.ceil(challenge.access_duration / 60)

  return (
    <YStack
      flex={1}
      backgroundColor="$nexaCanvas"
    >
      <ScrollView
        flex={1}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator
        contentContainerStyle={{ paddingBottom: 24 }}
      >
        <ScreenContainer maxWidth={680}>
          <ScreenHeader
            eyebrow="YOUR RECORDS, YOUR CHOICE"
            title="Access Request"
            description="Review who is asking, what they need, and how long access will last."
          />
          <Surface>
            <XStack
              gap="$3"
              alignItems="center"
            >
              <YStack
                padding="$3"
                backgroundColor="$nexaAccentSoft"
                borderRadius={12}
              >
                <UserCheck
                  size={24}
                  color="$nexaAccent"
                />
              </YStack>
              <YStack
                flex={1}
                gap="$1"
              >
                <Text
                  color="$nexaSecondary"
                  fontSize={13}
                >
                  Requesting Provider
                </Text>
                <SectionHeading>
                  {challenge.provider_name || 'Provider name unavailable'}
                </SectionHeading>
                <Paragraph color="$nexaSecondary">
                  {challenge.hospital_name || 'Facility name unavailable'}
                </Paragraph>
              </YStack>
            </XStack>
            <YStack
              borderTopWidth={1}
              borderColor="$nexaBorder"
              paddingTop="$3"
              gap="$1"
            >
              <Text
                fontWeight="700"
                color="$nexaText"
              >
                Purpose
              </Text>
              <Paragraph color="$nexaSecondary">{challenge.purpose}</Paragraph>
            </YStack>
          </Surface>
          <Surface>
            <SectionHeading>Data Requested</SectionHeading>
            <XStack
              flexWrap="wrap"
              gap="$2"
            >
              {scopeItems.map((item, index) => (
                <StatusBadge
                  key={`${item}-${index}`}
                  tone="info"
                >
                  {item}
                </StatusBadge>
              ))}
            </XStack>
            <XStack
              borderTopWidth={1}
              borderColor="$nexaBorder"
              paddingTop="$3"
              alignItems="center"
              gap="$3"
              flexWrap="wrap"
            >
              <Clock
                size={22}
                color="$nexaAccent"
              />
              <YStack
                flex={1}
                gap="$1"
              >
                <Text
                  color="$nexaSecondary"
                  fontSize={14}
                >
                  Access Duration
                </Text>
                <Text
                  color="$nexaText"
                  fontWeight="700"
                  fontSize={20}
                >
                  {accessMinutes} minute{accessMinutes !== 1 ? 's' : ''}
                </Text>
              </YStack>
            </XStack>
          </Surface>
          <Surface
            backgroundColor="$nexaWarningSoft"
            borderColor="$nexaWarning"
          >
            <Text
              color="$nexaWarning"
              fontWeight="700"
            >
              Request Expires In
            </Text>
            <Text
              color="$nexaWarning"
              fontSize={24}
              fontWeight="700"
            >
              {countdown}
            </Text>
            <Paragraph
              color="$nexaWarning"
              fontSize={14}
            >
              This is the time left to respond, separate from the access duration above.
            </Paragraph>
          </Surface>
          <Paragraph
            color="$nexaSecondary"
            fontSize={15}
            lineHeight={24}
          >
            Approve only if you recognize this request. Next, you will verify your approval with
            biometrics. Denying does not grant access.
          </Paragraph>
          {error !== null && (
            <InlineNotice
              title={error}
              tone="danger"
            />
          )}
        </ScreenContainer>
      </ScrollView>
      <YStack
        flexShrink={0}
        backgroundColor="$nexaSurface"
        borderTopWidth={1}
        borderColor="$nexaBorder"
        paddingHorizontal="$4"
        paddingTop="$3"
        paddingBottom={insets.bottom + 16}
      >
        <YStack
          width="100%"
          maxWidth={648}
          alignSelf="center"
          gap="$3"
        >
          <ActionButton
            intent="primary"
            disabled={expired || denying}
            onPress={handleApprove}
          >
            Approve
          </ActionButton>
          <ActionButton
            intent="danger"
            disabled={expired || denying}
            onPress={handleDeny}
          >
            {denying ? 'Denying...' : 'Deny'}
          </ActionButton>
        </YStack>
      </YStack>
    </YStack>
  )
}
