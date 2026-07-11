import { useRouter } from 'expo-router'
import { YStack, H2, Paragraph, Text, ScrollView, XStack, Separator, Button, Spinner } from 'tamagui'
import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../../utils/apiClient'
import SourceBadge from './badges/SourceBadge'
import RiskBadge, { type RiskLevel } from './badges/RiskBadge'

/**
 * Health timeline — chronological feed of clinical events with provenance.
 *
 * Fetches via apiClient.get('/api/v2/patient/me/timeline').
 * Backend: GET /api/v2/patient/me/timeline
 * Response: { patient_id, events: TimelineEntry[], next_cursor }
 *
 * Each event shows its source ("Manual entry" vs "AI-extracted, 91% confidence")
 * with a colour-coded SourceBadge.  AI-extracted events display a confidence
 * badge.  Risk levels are shown with a colour-coded RiskBadge.
 *
 * Backend event types: VITALS, MEDICATION, LAB_RESULT, ALLERGY, DOCUMENT, ENCOUNTER
 */

interface TimelineEntry {
  event_id: string
  event_type: string
  title: string
  summary: string
  description?: string
  event_date?: string
  occurred_at: string
  source: 'manual' | 'ai_extracted' | string
  source_display?: string
  provenance?: string
  confidence?: number | null
  risk_level?: string | null
  review_status?: string
  badges?: string[]
}

interface PatientTimelineScreenProps {
  timeline?: TimelineEntry[]
}

const CATEGORY_ICONS: Record<string, string> = {
  VITALS: '❤️',
  MEDICATION: '💊',
  LAB_RESULT: '🔬',
  ALLERGY: '⚠️',
  DOCUMENT: '📄',
  ENCOUNTER: '🏥',
  DIAGNOSIS: '🏥',
}

const CATEGORY_LABELS: Record<string, string> = {
  VITALS: 'Vitals',
  MEDICATION: 'Medication',
  LAB_RESULT: 'Lab Result',
  ALLERGY: 'Allergy',
  DOCUMENT: 'Document',
  ENCOUNTER: 'Encounter',
  DIAGNOSIS: 'Diagnosis',
}

export default function PatientTimelineScreen({ timeline: initialTimeline }: PatientTimelineScreenProps) {
  const router = useRouter()
  const [timeline, setTimeline] = useState<TimelineEntry[]>(initialTimeline ?? [])
  const [loading, setLoading] = useState(!initialTimeline)
  const [error, setError] = useState<string | null>(null)

  const fetchTimeline = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const { data } = await apiClient.get('/api/v2/patient/me/timeline')
      setTimeline(data?.events ?? [])
    } catch {
      setError('Failed to load health timeline.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (initialTimeline) return
    fetchTimeline()
  }, [initialTimeline, fetchTimeline])

  const handleRetry = () => {
    fetchTimeline()
  }

  // Group events by date using occurred_at
  const grouped = timeline.reduce<Record<string, TimelineEntry[]>>((acc, event) => {
    const dateKey = new Date(event.occurred_at || event.event_date).toLocaleDateString('en-IN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
    acc[dateKey] = acc[dateKey] ?? []
    acc[dateKey].push(event)
    return acc
  }, {})

  return (
    <YStack f={1} bg="$background">
      <YStack px="$4" pt="$4" pb="$2">
        <H2 col="$color" size="$7">Health Timeline</H2>
        <Paragraph col="$colorSubdued" size="$3">
          Your clinical events, consent-gated and de-identified.
        </Paragraph>
      </YStack>

      <ScrollView contentContainerStyle={{ padding: 16, gap: 16 }}>
        {/* Loading state */}
        {loading && (
          <YStack ai="center" py="$8" gap="$3">
            <Spinner size="large" color="$blue10" />
            <Paragraph col="$colorSubdued" size="$4">Loading timeline...</Paragraph>
          </YStack>
        )}

        {/* Error state */}
        {error && (
          <YStack ai="center" py="$8" gap="$2">
            <Text fontSize={36}>⚠️</Text>
            <Paragraph col="$red10" size="$4" ta="center">{error}</Paragraph>
            <Button size="$3" chromeless onPress={handleRetry}>
              Retry
            </Button>
          </YStack>
        )}

        {/* Empty state */}
        {!loading && !error && timeline.length === 0 && (
          <YStack ai="center" py="$8" gap="$2">
            <Text fontSize={48}>📊</Text>
            <Paragraph col="$colorSubdued" size="$4" ta="center">
              No clinical events yet.
            </Paragraph>
            <Paragraph col="$colorSubdued" size="$3" ta="center" o={0.6}>
              When your provider uploads documents, your timeline will populate here.
            </Paragraph>
          </YStack>
        )}

        {/* Grouped timeline events */}
        {Object.entries(grouped).map(([dateKey, events]) => (
          <YStack key={dateKey} gap="$2">
            <XStack ai="center" gap="$3">
              <Separator f={1} />
              <Text col="$colorSubdued" size="$2" fontWeight="600" textTransform="uppercase" letterSpacing={1}>
                {dateKey}
              </Text>
              <Separator f={1} />
            </XStack>

            {events.map((event) => {
              const icon = CATEGORY_ICONS[event.event_type] ?? '📋'
              const label = CATEGORY_LABELS[event.event_type] ?? event.event_type
              const isAbnormal = event.summary?.toLowerCase().includes('abnormal')
                || event.description?.toLowerCase().includes('abnormal')
              const riskLevel = event.risk_level as RiskLevel | null

              return (
                <YStack key={event.event_id} bg="$backgroundHover" br="$4" p="$3" gap="$2">
                  {/* Category icon + title + abnormal flag + risk badge */}
                  <XStack ai="center" gap="$2" fw="wrap">
                    <Text fontSize={18}>{icon}</Text>
                    <YStack f={1}>
                      <Text col="$color" fontWeight="600" size="$4">{event.title}</Text>
                      <Paragraph col="$colorSubdued" size="$2">
                        {label}
                      </Paragraph>
                    </YStack>
                    {isAbnormal && (
                      <YStack bg="$red5" br="$2" px="$2" py="$1">
                        <Text col="$red10" size="$2" fontWeight="600">ABNORMAL</Text>
                      </YStack>
                    )}
                    {riskLevel && (
                      <RiskBadge level={riskLevel} />
                    )}
                  </XStack>

                  {/* Summary / description */}
                  <Text col="$color" size="$4">{event.summary}</Text>

                  {/* Provenance: source badge */}
                  <XStack ai="center" gap="$2">
                    <SourceBadge
                      source={event.source}
                      confidence={event.confidence != null ? Math.round(event.confidence * 100) : undefined}
                    />
                  </XStack>

                  {/* Source display text from backend */}
                  {event.source_display && (
                    <Paragraph col="$colorSubdued" size="$2" o={0.5}>
                      {event.source_display}
                    </Paragraph>
                  )}
                </YStack>
              )
            })}
          </YStack>
        ))}
      </ScrollView>

      <YStack p="$4" bc="$background">
        <Separator />
        <Button chromeless size="$3" onPress={() => router.push('/patient/access-history')}>
          ← Access History
        </Button>
      </YStack>
    </YStack>
  )
}
