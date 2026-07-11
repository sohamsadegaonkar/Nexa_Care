/**
 * Review queue screen — list all flagged fields needing human adjudication.
 *
 * Fetches GET /api/v2/pipeline/review-queue and displays items grouped by
 * job. Each item shows document title, flagged field count, highest risk
 * level, and queued timestamp.
 *
 * ALPHA: This is an alpha implementation. Filtering and sorting are not
 * yet implemented. Queue items may use placeholder data.
 *
 * SECURITY:
 * - All requests go through the shared NexaApiClient — no raw fetch/axios.
 * - Consent token passed as X-Consent-Token header.
 * - Session guard: must be authenticated.
 *
 * Route: /doctor/pipeline/review-queue?consent_token=...
 */

'use client'

import {
  YStack, H2, Paragraph, Button, Text, Spinner, Card, XStack, Separator, ScrollView,
} from '@my/ui'
import { useState, useEffect, useCallback } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { NexaApiClient, type ReviewQueueListResponse, ApiError } from '../../utils/apiClient'
import { useProviderAuth } from '../doctor/ProviderAuthContext'

type RiskLevel = 'LOW_RISK' | 'MEDIUM_RISK' | 'HIGH_RISK' | 'CRITICAL_RISK'

/** Risk level → colour mapping. */
const RISK_COLORS: Record<RiskLevel, { bg: string; text: string }> = {
  LOW_RISK: { bg: '$green4', text: '$green10' },
  MEDIUM_RISK: { bg: '$orange4', text: '$orange10' },
  HIGH_RISK: { bg: '$red4', text: '$red10' },
  CRITICAL_RISK: { bg: '$red4', text: '$red10' },
}

const RISK_ICONS: Record<RiskLevel, string> = {
  LOW_RISK: '✓',
  MEDIUM_RISK: '⚠',
  HIGH_RISK: '⛔',
  CRITICAL_RISK: '🚨',
}

export function ReviewQueueScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { isAuthenticated } = useProviderAuth()

  const consentToken = searchParams.get('consent_token') ?? ''
  const hospitalId = searchParams.get('hospital_id') ?? ''

  const [items, setItems] = useState<ReviewQueueListResponse['items']>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // ── Session guard ────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$6">🔒 Session Required</Text>
        <Paragraph col="$colorSubdued" size="$3">
          Please log in to view the review queue.
        </Paragraph>
        <Button theme="blue" onPress={() => router.push('/doctor/login')}>
          Go to Login
        </Button>
      </YStack>
    )
  }

  // ── Fetch review queue ───────────────────────────────────────────────
  const fetchQueue = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await NexaApiClient.getReviewQueue(hospitalId, consentToken)
      setItems(data.items)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          router.push('/doctor/login')
          return
        }
        if (err.status === 403) {
          setError('Consent required for clinical review.')
          return
        }
      }
      setError('Failed to load review queue.')
    } finally {
      setLoading(false)
    }
  }, [hospitalId, consentToken, router])

  useEffect(() => {
    fetchQueue()
  }, [fetchQueue])

  // ── Missing consent token ────────────────────────────────────────────
  if (!consentToken) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$6">🔒 Consent Required</Text>
        <Paragraph col="$colorSubdued" size="$3">
          An active consent grant with clinical review scope is required.
        </Paragraph>
        <Button theme="blue" onPress={() => router.push('/doctor/request-consent')}>
          Request Consent
        </Button>
      </YStack>
    )
  }

  return (
    <YStack f={1} bg="$background" p="$6" gap="$4" mw={900} mx="auto">
      {/* ALPHA badge + header */}
      <XStack ai="center" gap="$2">
        <H2 col="$color" size="$7">Review Queue</H2>
        <Card bg="$orange4" br="$4" px="$2" py="$1">
          <Text col="$orange10" size="$2" fontWeight="700" textTransform="uppercase">
            ALPHA
          </Text>
        </Card>
      </XStack>

      <Paragraph col="$colorSubdued" size="$3">
        ALPHA · AI-assisted extraction results require clinical verification
        before commitment.
      </Paragraph>

      <Separator />

      {/* Loading state */}
      {loading && (
        <YStack ai="center" py="$8">
          <Spinner size="large" color="$blue10" />
          <Text col="$colorSubdued" size="$3" mt="$2">Loading review queue…</Text>
        </YStack>
      )}

      {/* Error state */}
      {error && !loading && (
        <YStack bg="$red4" br="$3" p="$4" gap="$2">
          <Text col="$red10" size="$4" fontWeight="600">{error}</Text>
          <Button size="$2" chromeless onPress={fetchQueue}>Retry</Button>
        </YStack>
      )}

      {/* Empty state */}
      {!loading && !error && items.length === 0 && (
        <YStack ai="center" py="$8">
          <Text col="$colorSubdued" size="$5">No items pending review.</Text>
          <Paragraph col="$colorSubdued" size="$3" mt="$2">
            All flagged fields have been adjudicated or no documents have been
            processed yet.
          </Paragraph>
        </YStack>
      )}

      {/* Queue items */}
      {!loading && !error && items.length > 0 && (
        <ScrollView>
          <YStack gap="$3">
            {items.map((item) => {
              const risk = item.highest_risk_level as RiskLevel
              const colors = RISK_COLORS[risk] ?? RISK_COLORS.MEDIUM_RISK
              const icon = RISK_ICONS[risk] ?? '⚠'

              return (
                <Card
                  key={item.review_item_id}
                  p="$4"
                  bg="$backgroundHover"
                  br="$4"
                  hoverStyle={{ bg: '$backgroundFocus' }}
                  pressStyle={{ bg: '$backgroundPress' }}
                  onPress={() =>
                    router.push(
                      `/doctor/pipeline/review/${item.job_id}?patient_id=${item.patient_id}&consent_token=${consentToken}`,
                    )
                  }
                >
                  <XStack jc="space-between" ai="center">
                    <YStack gap="$1" f={1}>
                      <Text col="$color" size="$4" fontWeight="600">
                        {item.document_title}
                      </Text>
                      <XStack gap="$3" ai="center">
                        <Text col="$colorSubdued" size="$2">
                          Patient: {item.patient_id}
                        </Text>
                        <Text col="$colorSubdued" size="$2">
                          {item.flagged_fields_count} field{item.flagged_fields_count !== 1 ? 's' : ''} flagged
                        </Text>
                        <Text col="$colorSubdued" size="$2">
                          Queued: {new Date(item.queued_at).toLocaleString()}
                        </Text>
                      </XStack>
                    </YStack>

                    {/* Risk badge */}
                    <Card bg={colors.bg} br="$4" px="$3" py="$2">
                      <Text col={colors.text} size="$3" fontWeight="700">
                        {icon} {risk.replace('_', ' ')}
                      </Text>
                    </Card>
                  </XStack>
                </Card>
              )
            })}
          </YStack>
        </ScrollView>
      )}

      {/* Navigation */}
      <Separator />
      <Button chromeless onPress={() => router.push('/doctor/dashboard')}>
        ← Back to Dashboard
      </Button>
    </YStack>
  )
}
