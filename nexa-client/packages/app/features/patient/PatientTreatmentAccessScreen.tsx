import React, { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'solito/navigation'
import {
  Button,
  H2,
  H3,
  Paragraph,
  Separator,
  Spinner,
  Text,
  XStack,
  YStack,
} from 'tamagui'
import {
  NexaApiClient,
  type PatientConsentHistoryItem,
} from '../../utils/apiClient'

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function PatientTreatmentAccessScreen() {
  const router = useRouter()
  const [items, setItems] = useState<PatientConsentHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [confirmingRef, setConfirmingRef] = useState<string | null>(null)
  const [revokingRef, setRevokingRef] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<{
    tone: 'success' | 'error'
    text: string
  } | null>(null)

  const isMountedRef = React.useRef(true)

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const loadHistory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await NexaApiClient.getSelfConsentHistory()
      if (isMountedRef.current) {
        setItems(data)
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(
          err instanceof Error ? err.message : 'Unable to load treatment access history.'
        )
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    void loadHistory()
  }, [loadHistory])

  const handleRevoke = async (publicRef: string) => {
    if (revokingRef) return
    setRevokingRef(publicRef)
    setActionMessage(null)
    try {
      await NexaApiClient.revokeSelfConsentGrant(publicRef)
      if (isMountedRef.current) {
        setConfirmingRef(null)
        setActionMessage({
          tone: 'success',
          text: 'Treatment access successfully revoked.',
        })
      }
      await loadHistory()
    } catch (err) {
      if (isMountedRef.current) {
        setActionMessage({
          tone: 'error',
          text: err instanceof Error ? err.message : 'Failed to revoke access. Please try again.',
        })
      }
    } finally {
      if (isMountedRef.current) {
        setRevokingRef(null)
      }
    }
  }

  // Filter for treatment sessions based on exact scope membership or server classification
  const treatmentItems = items.filter(
    (item) =>
      item.is_treatment_session ||
      item.scope.includes('treatment') ||
      item.scope.includes('treatment.session.v1')
  )

  const activeGrants = treatmentItems.filter((item) => item.status === 'active')
  const historicalGrants = treatmentItems.filter(
    (item) => item.status === 'expired' || item.status === 'revoked'
  )

  return (
    <YStack flex={1} backgroundColor="$background" padding="$4" gap="$4">
      {/* Header */}
      <XStack alignItems="center" justifyContent="space-between" flexWrap="wrap" gap="$2">
        <YStack gap="$1">
          <H2 color="$color" size="$7">
            Treatment Access
          </H2>
          <Paragraph color="$color10" size="$3">
            Review and control clinician access granted during treatment sessions.
          </Paragraph>
        </YStack>
        <Button
          size="$3"
          chromeless
          minHeight={44}
          onPress={() => router.push('/patient/dashboard')}
          accessibilityLabel="Back to Dashboard"
          aria-label="Back to Dashboard"
        >
          ← Dashboard
        </Button>
      </XStack>

      <Separator />

      {/* Live Region for Action Announcements */}
      {actionMessage && (
        <YStack
          backgroundColor={actionMessage.tone === 'success' ? '$green2' : '$red2'}
          borderColor={actionMessage.tone === 'success' ? '$green7' : '$red7'}
          borderWidth={1}
          borderRadius="$3"
          padding="$3"
          role="alert"
          accessibilityLiveRegion="polite"
        >
          <Paragraph
            color={actionMessage.tone === 'success' ? '$green11' : '$red11'}
            fontWeight="600"
            size="$3"
          >
            {actionMessage.text}
          </Paragraph>
        </YStack>
      )}

      {/* Loading & Error States */}
      {loading && (
        <YStack alignItems="center" justifyContent="center" padding="$6" gap="$3">
          <Spinner size="large" color="$color11" />
          <Paragraph color="$color10">Loading treatment access records...</Paragraph>
        </YStack>
      )}

      {error && !loading && (
        <YStack
          backgroundColor="$red2"
          borderColor="$red7"
          borderWidth={1}
          borderRadius="$4"
          padding="$4"
          gap="$2"
          role="alert"
        >
          <Paragraph color="$red11" fontWeight="600">
            {error}
          </Paragraph>
          <Button
            theme="red"
            size="$3"
            minHeight={44}
            alignSelf="flex-start"
            onPress={() => void loadHistory()}
            accessibilityLabel="Try Again"
            aria-label="Try Again"
          >
            Try Again
          </Button>
        </YStack>
      )}

      {/* Content */}
      {!loading && !error && (
        <YStack gap="$5">
          {/* Active Treatment Access */}
          <YStack gap="$3">
            <H3 color="$color" size="$5">
              Active Treatment Access
            </H3>
            {activeGrants.length === 0 ? (
              <YStack
                backgroundColor="$backgroundHover"
                borderRadius="$4"
                padding="$4"
                alignItems="center"
              >
                <Paragraph color="$color10">
                  No active treatment sessions. You have not granted ongoing access to any clinician.
                </Paragraph>
              </YStack>
            ) : (
              activeGrants.map((grant) => {
                const isConfirming = confirmingRef === grant.public_ref
                const isRevoking = revokingRef === grant.public_ref

                return (
                  <YStack
                    key={grant.public_ref || grant.id}
                    backgroundColor="$backgroundHover"
                    borderColor="$green8"
                    borderWidth={1}
                    borderRadius="$4"
                    padding="$4"
                    gap="$3"
                    role="region"
                    accessibilityLabel={`Active treatment access for ${grant.purpose}`}
                  >
                    <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
                      <XStack gap="$2" alignItems="center">
                        <Text fontSize={20}>🩺</Text>
                        <YStack>
                          <Text color="$color" fontSize="$4" fontWeight="700">
                            Active Treatment Access
                          </Text>
                          <Paragraph color="$color10" size="$2">
                            Purpose: {grant.purpose}
                          </Paragraph>
                        </YStack>
                      </XStack>
                      <XStack
                        backgroundColor="$green3"
                        paddingHorizontal="$2.5"
                        paddingVertical="$1"
                        borderRadius="$2"
                      >
                        <Text color="$green11" fontSize="$2" fontWeight="700">
                          ACTIVE
                        </Text>
                      </XStack>
                    </XStack>

                    <XStack gap="$4" flexWrap="wrap">
                      <YStack>
                        <Paragraph color="$color10" size="$1" textTransform="uppercase">
                          Granted
                        </Paragraph>
                        <Text color="$color" fontSize="$3">
                          {formatDate(grant.issued_at)}
                        </Text>
                      </YStack>
                      <YStack>
                        <Paragraph color="$color10" size="$1" textTransform="uppercase">
                          Expires
                        </Paragraph>
                        <Text color="$color" fontSize="$3" fontWeight="600">
                          {formatDate(grant.expires_at)}
                        </Text>
                      </YStack>
                    </XStack>

                    {/* Revocation Section */}
                    {isConfirming ? (
                      <YStack
                        backgroundColor="$red2"
                        borderColor="$red6"
                        borderWidth={1}
                        borderRadius="$3"
                        padding="$3"
                        gap="$2"
                      >
                        <Paragraph color="$red11" size="$2" fontWeight="600">
                          Are you sure you want to revoke this treatment access? The clinician will no longer be able to record vitals or conduct treatment operations under this session.
                        </Paragraph>
                        <XStack gap="$2">
                          <Button
                            theme="red"
                            size="$3"
                            minHeight={44}
                            disabled={isRevoking}
                            onPress={() => void handleRevoke(grant.public_ref)}
                            accessibilityLabel="Confirm revoke treatment access"
                            aria-label="Confirm revoke treatment access"
                          >
                            {isRevoking ? 'Revoking...' : 'Yes, Revoke Access'}
                          </Button>
                          <Button
                            chromeless
                            size="$3"
                            minHeight={44}
                            disabled={isRevoking}
                            onPress={() => setConfirmingRef(null)}
                            accessibilityLabel="Cancel revocation"
                            aria-label="Cancel revocation"
                          >
                            Cancel
                          </Button>
                        </XStack>
                      </YStack>
                    ) : (
                      <XStack justifyContent="flex-end">
                        <Button
                          theme="red"
                          size="$3"
                          minHeight={44}
                          disabled={Boolean(revokingRef)}
                          onPress={() => setConfirmingRef(grant.public_ref)}
                          accessibilityLabel={`Revoke treatment access for ${grant.purpose}`}
                          aria-label={`Revoke treatment access for ${grant.purpose}`}
                        >
                          Revoke Access
                        </Button>
                      </XStack>
                    )}
                  </YStack>
                )
              })
            )}
          </YStack>

          {/* Expired / Revoked Treatment Access */}
          <YStack gap="$3">
            <H3 color="$color" size="$5">
              Past Treatment Access
            </H3>
            {historicalGrants.length === 0 ? (
              <YStack
                backgroundColor="$backgroundHover"
                borderRadius="$4"
                padding="$4"
                alignItems="center"
              >
                <Paragraph color="$color10">No past treatment sessions on file.</Paragraph>
              </YStack>
            ) : (
              historicalGrants.map((grant) => {
                const isRevoked = grant.status === 'revoked'

                return (
                  <YStack
                    key={grant.public_ref || grant.id}
                    backgroundColor="$backgroundHover"
                    borderRadius="$4"
                    padding="$3.5"
                    gap="$2"
                    opacity={0.85}
                  >
                    <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
                      <YStack>
                        <Text color="$color" fontSize="$3" fontWeight="600">
                          {isRevoked ? 'Revoked Treatment Access' : 'Expired Treatment Access'}
                        </Text>
                        <Paragraph color="$color10" size="$2">
                          Purpose: {grant.purpose}
                        </Paragraph>
                      </YStack>
                      <XStack
                        backgroundColor={isRevoked ? '$red3' : '$gray4'}
                        paddingHorizontal="$2"
                        paddingVertical="$0.5"
                        borderRadius="$2"
                      >
                        <Text
                          color={isRevoked ? '$red11' : '$gray11'}
                          fontSize="$1"
                          fontWeight="700"
                        >
                          {isRevoked ? 'REVOKED' : 'EXPIRED'}
                        </Text>
                      </XStack>
                    </XStack>

                    <XStack gap="$4" flexWrap="wrap">
                      <YStack>
                        <Paragraph color="$color10" size="$1">
                          Granted: {formatDate(grant.issued_at)}
                        </Paragraph>
                      </YStack>
                      {grant.revoked_at ? (
                        <YStack>
                          <Paragraph color="$color10" size="$1">
                            Revoked: {formatDate(grant.revoked_at)}
                          </Paragraph>
                        </YStack>
                      ) : (
                        <YStack>
                          <Paragraph color="$color10" size="$1">
                            Expired: {formatDate(grant.expires_at)}
                          </Paragraph>
                        </YStack>
                      )}
                    </XStack>
                  </YStack>
                )
              })
            )}
          </YStack>
        </YStack>
      )}
    </YStack>
  )
}
