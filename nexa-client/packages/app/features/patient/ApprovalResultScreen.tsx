import { useLocalSearchParams } from 'expo-router'
import { YStack, H2, Paragraph, Button, Text, XStack, Separator } from 'tamagui'
import { useState, useEffect } from 'react'
import { NexaApiClient } from '../../utils/apiClient'
import { useResetToPatientAccessHistory } from '../../hooks/useResetToPatientAccessHistory'

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
  const resetToAccessHistory = useResetToPatientAccessHistory()
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

  useEffect(() => {
    if (isExpired) resetToAccessHistory()
  }, [isExpired, resetToAccessHistory])

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
      resetToAccessHistory()
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
        flex={1}
        backgroundColor="$background"
        padding="$4"
        gap="$4"
        justifyContent="center"
        alignItems="center"
      >
        <Text fontSize={64}>⏰</Text>
        <H2
          color="$color"
          textAlign="center"
        >
          Request Expired
        </H2>
        <Paragraph
          color="$color10"
          textAlign="center"
          size="$4"
          maxWidth={320}
        >
          This request has expired. No action was taken and no data was shared.
        </Paragraph>
        <Button
          theme="blue"
          size="$4"
          marginTop="$2"
          onPress={resetToAccessHistory}
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
        flex={1}
        backgroundColor="$background"
        padding="$4"
        gap="$4"
        justifyContent="center"
        alignItems="center"
      >
        <Text fontSize={64}>✅</Text>

        <H2
          color="$green10"
          textAlign="center"
        >
          Access Granted
        </H2>

        <Paragraph
          color="$color10"
          textAlign="center"
          size="$4"
          maxWidth={320}
        >
          Access granted to {providerName}. The doctor can now view the approved data until access
          expires.
        </Paragraph>

        <YStack
          backgroundColor="$backgroundHover"
          borderRadius="$4"
          padding="$4"
          gap="$3"
          width="100%"
          maxWidth={360}
        >
          <YStack>
            <Paragraph
              color="$color10"
              size="$2"
              textTransform="uppercase"
              letterSpacing={1}
            >
              Provider
            </Paragraph>
            <Text
              color="$color"
              fontSize="$4"
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
                  color="$color10"
                  size="$2"
                  textTransform="uppercase"
                  letterSpacing={1}
                >
                  Approved Data
                </Paragraph>
                <YStack
                  gap="$1"
                  marginTop="$1"
                >
                  {scope.map((item) => (
                    <XStack
                      key={item}
                      gap="$2"
                      alignItems="center"
                    >
                      <Text
                        color="$green10"
                        fontSize="$3"
                      >
                        ✓
                      </Text>
                      <Text
                        color="$color"
                        fontSize="$3"
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
            justifyContent="space-between"
            alignItems="center"
          >
            <Paragraph
              color="$color10"
              size="$2"
              textTransform="uppercase"
              letterSpacing={1}
            >
              Expires In
            </Paragraph>
            <Text
              color="$orange10"
              fontSize="$5"
              fontWeight="700"
              style={{ fontFamily: 'monospace' }}
            >
              {remaining || '—'}
            </Text>
          </XStack>
        </YStack>

        <YStack
          gap="$3"
          width="100%"
          marginTop="$2"
        >
          <Button
            theme="blue"
            size="$4"
            onPress={resetToAccessHistory}
          >
            View Access History
          </Button>

          <Button
            size="$4"
            chromeless
            theme="red"
            disabled={revoking}
            onPress={handleRevoke}
          >
            {revoking ? 'Revoking...' : 'Revoke Access Now'}
          </Button>
          {revokeError ? (
            <Paragraph
              color="$red10"
              textAlign="center"
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
      flex={1}
      backgroundColor="$background"
      padding="$4"
      gap="$4"
      justifyContent="center"
      alignItems="center"
    >
      <Text fontSize={64}>❌</Text>

      <H2
        color="$red10"
        textAlign="center"
      >
        Access Denied
      </H2>

      <Paragraph
        color="$color10"
        textAlign="center"
        size="$4"
        maxWidth={320}
      >
        Access denied. The doctor has been notified. No data was shared.
      </Paragraph>

      <Button
        theme="blue"
        size="$4"
        marginTop="$2"
        onPress={resetToAccessHistory}
      >
        Go to Access History
      </Button>
    </YStack>
  )
}
