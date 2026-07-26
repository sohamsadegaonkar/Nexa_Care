import { useRouter, useLocalSearchParams } from 'expo-router'
import { YStack, H2, Paragraph, Button, Text, XStack, Separator } from 'tamagui'
import { useState, useEffect } from 'react'
import { NexaApiClient } from '../../utils/apiClient'

/**
 * Approval result screen — shows approved / denied / expired state.
 *
 * Approved: green "Access granted to Dr. [name]" with countdown and revoke.
 * Denied: red "Access denied. The doctor has been notified."
 * Expired: "This request has expired."
 *
 * All data comes from route params — no hardcoded patient/provider IDs.
 */

export default function ApprovalResultScreen() {
  const router = useRouter()
  const params = useLocalSearchParams<{
    requestId?: string
    decision?: string
    providerName?: string
    scope?: string
    expiresAt?: string
  }>()

  const decision = params.decision ?? ''
  const isApproved = decision === 'approved'
  const isDenied = decision === 'denied'
  const isExpired = !isApproved && !isDenied
  const providerName = params.providerName ?? 'Provider'
  const scope = params.scope ? params.scope.split(',') : []
  const expiresAt = params.expiresAt
  const [remaining, setRemaining] = useState('')
  const [revoking, setRevoking] = useState(false)
  const [revokeError, setRevokeError] = useState<string | null>(null)

  // Countdown timer for approved grants
  useEffect(() => {
    if (!expiresAt || !isApproved) return
    const update = () => {
      const diff = new Date(expiresAt).getTime() - Date.now()
      if (diff <= 0) {
        setRemaining('Expired')
        return
      }
      const h = Math.floor(diff / (1000 * 60 * 60))
      const m = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))
      const s = Math.floor((diff % (1000 * 60)) / 1000)
      setRemaining(`${h}h ${m}m ${s}s`)
    }
    update()
    const timer = setInterval(update, 1000)
    return () => clearInterval(timer)
  }, [expiresAt, isApproved])

  const handleRevoke = async () => {
    if (!params.requestId) {
      setRevokeError('This consent request is unavailable. Return to Access History and try again.')
      return
    }
    setRevoking(true)
    setRevokeError(null)
    try {
      await NexaApiClient.revokeApprovedAccess(params.requestId)
      router.replace('/patient/access-history')
    } catch (error) {
      setRevokeError(
        error instanceof Error ? error.message : 'Unable to revoke access. Please try again.'
      )
    } finally {
      setRevoking(false)
    }
  }

  // ── Render: Expired ──────────────────────────────────────────────────
  if (isExpired) {
    return (
      <YStack
        f={1}
        bg="$background"
        p="$4"
        gap="$4"
        jc="center"
        ai="center"
      >
        <Text fontSize={64}>⏰</Text>
        <H2
          col="$color"
          ta="center"
        >
          Request Expired
        </H2>
        <Paragraph
          col="$colorSubdued"
          ta="center"
          size="$4"
          mw={320}
        >
          This request has expired. No action was taken and no data was shared.
        </Paragraph>
        <Button
          theme="blue"
          size="$4"
          mt="$2"
          onPress={() => router.replace('/patient/access-history')}
        >
          Go to Access History
        </Button>
      </YStack>
    )
  }

  // ── Render: Approved ─────────────────────────────────────────────────
  if (isApproved) {
    return (
      <YStack
        f={1}
        bg="$background"
        p="$4"
        gap="$4"
        jc="center"
        ai="center"
      >
        <Text fontSize={64}>✅</Text>

        <H2
          col="$green10"
          ta="center"
        >
          Access Granted
        </H2>

        <Paragraph
          col="$colorSubdued"
          ta="center"
          size="$4"
          mw={320}
        >
          Access granted to {providerName}. The doctor can now view the approved data until access
          expires.
        </Paragraph>

        <YStack
          bg="$backgroundHover"
          br="$4"
          p="$4"
          gap="$3"
          w="100%"
          mw={360}
        >
          <YStack>
            <Paragraph
              col="$colorSubdued"
              size="$2"
              textTransform="uppercase"
              letterSpacing={1}
            >
              Provider
            </Paragraph>
            <Text
              col="$color"
              size="$4"
              fontWeight="600"
            >
              {providerName}
            </Text>
          </YStack>

          {scope.length > 0 && (
            <>
              <Separator />
              <YStack>
                <Paragraph
                  col="$colorSubdued"
                  size="$2"
                  textTransform="uppercase"
                  letterSpacing={1}
                >
                  Approved Data
                </Paragraph>
                <YStack
                  gap="$1"
                  mt="$1"
                >
                  {scope.map((item) => (
                    <XStack
                      key={item}
                      gap="$2"
                      ai="center"
                    >
                      <Text
                        col="$green10"
                        size="$3"
                      >
                        ✓
                      </Text>
                      <Text
                        col="$color"
                        size="$3"
                      >
                        {item}
                      </Text>
                    </XStack>
                  ))}
                </YStack>
              </YStack>
            </>
          )}

          <Separator />

          <XStack
            jc="space-between"
            ai="center"
          >
            <Paragraph
              col="$colorSubdued"
              size="$2"
              textTransform="uppercase"
              letterSpacing={1}
            >
              Expires In
            </Paragraph>
            <Text
              col="$orange10"
              size="$5"
              fontWeight="700"
              fontFamily="$mono"
            >
              {remaining || '—'}
            </Text>
          </XStack>
        </YStack>

        <YStack
          gap="$3"
          w="100%"
          mt="$2"
        >
          <Button
            theme="blue"
            size="$4"
            onPress={() => router.replace('/patient/access-history')}
          >
            View Access History
          </Button>

          <Button
            size="$4"
            chromeless
            color="$red10"
            disabled={revoking}
            onPress={handleRevoke}
          >
            {revoking ? 'Revoking...' : 'Revoke Access Now'}
          </Button>
          {revokeError ? (
            <Paragraph
              col="$red10"
              ta="center"
              size="$3"
            >
              {revokeError}
            </Paragraph>
          ) : null}
        </YStack>
      </YStack>
    )
  }

  // ── Render: Denied ───────────────────────────────────────────────────
  return (
    <YStack
      f={1}
      bg="$background"
      p="$4"
      gap="$4"
      jc="center"
      ai="center"
    >
      <Text fontSize={64}>❌</Text>

      <H2
        col="$red10"
        ta="center"
      >
        Access Denied
      </H2>

      <Paragraph
        col="$colorSubdued"
        ta="center"
        size="$4"
        mw={320}
      >
        Access denied. The doctor has been notified. No data was shared.
      </Paragraph>

      <Button
        theme="blue"
        size="$4"
        mt="$2"
        onPress={() => router.replace('/patient/access-history')}
      >
        Go to Access History
      </Button>
    </YStack>
  )
}
