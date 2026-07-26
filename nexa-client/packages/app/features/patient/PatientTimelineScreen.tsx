import { useRouter } from 'expo-router'
import { Button, H2, Paragraph, Separator, Spinner, Text, XStack, YStack } from 'tamagui'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { RefreshControl, SectionList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { apiClient } from '../../utils/apiClient'
import SourceBadge from './badges/SourceBadge'
import RiskBadge, { type RiskLevel } from './badges/RiskBadge'

interface TimelineEntry {
  event_id: string
  event_type: string
  title: string
  summary: string
  description?: string
  event_date?: string
  occurred_at?: string
  source: 'manual' | 'ai_extracted' | string
  source_display?: string
  confidence?: number | null
  risk_level?: string | null
}

interface TimelineResponse {
  events: TimelineEntry[]
  next_cursor: string | null
}

interface PatientTimelineScreenProps {
  timeline?: TimelineEntry[]
}

type TimelineSection = {
  title: string
  data: TimelineEntry[]
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

function sectionTitle(event: TimelineEntry): string {
  for (const candidate of [event.occurred_at, event.event_date]) {
    if (!candidate) continue
    const date = new Date(candidate)
    if (Number.isNaN(date.getTime())) continue
    return date.toLocaleDateString('en-IN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }
  return 'Date unavailable'
}

function buildSections(timeline: TimelineEntry[]): TimelineSection[] {
  const grouped = new Map<string, TimelineEntry[]>()
  for (const event of timeline) {
    const title = sectionTitle(event)
    const events = grouped.get(title) ?? []
    events.push(event)
    grouped.set(title, events)
  }
  return Array.from(grouped, ([title, data]) => ({ title, data }))
}

function normalizeTimelineResponse(response: unknown): TimelineResponse {
  if (response && typeof response === 'object') {
    const direct = response as Partial<TimelineResponse>
    if (Array.isArray(direct.events)) {
      return direct as TimelineResponse
    }

    const data = (response as { data?: unknown }).data
    if (data && typeof data === 'object') {
      const wrapped = data as Partial<TimelineResponse>
      if (Array.isArray(wrapped.events)) {
        return wrapped as TimelineResponse
      }
    }
  }

  throw new Error('INVALID_TIMELINE_RESPONSE')
}

export default function PatientTimelineScreen({
  timeline: initialTimeline,
}: PatientTimelineScreenProps) {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [timeline, setTimeline] = useState<TimelineEntry[]>(initialTimeline ?? [])
  const [initialLoading, setInitialLoading] = useState(initialTimeline === undefined)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timelineRef = useRef(initialTimeline ?? [])
  const requestInFlightRef = useRef(false)

  const fetchTimeline = useCallback(async (mode: 'initial' | 'refresh') => {
    if (requestInFlightRef.current) return
    requestInFlightRef.current = true
    if (timelineRef.current.length === 0) setInitialLoading(true)
    else setRefreshing(true)
    setError(null)

    try {
      const response = (await apiClient.get('/api/v2/patient/me/timeline')) as unknown
      const payload = normalizeTimelineResponse(response)
      timelineRef.current = payload.events
      setTimeline(payload.events)
      setError(null)
    } catch (caught) {
      setError(
        caught instanceof Error && caught.message === 'INVALID_TIMELINE_RESPONSE'
          ? 'Health timeline returned an invalid response.'
          : 'Failed to load health timeline.'
      )
    } finally {
      requestInFlightRef.current = false
      setInitialLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    if (initialTimeline !== undefined) return
    void fetchTimeline('initial')
  }, [initialTimeline, fetchTimeline])

  const sections = useMemo(() => buildSections(timeline), [timeline])

  const renderTimelineItem = ({ item: event }: { item: TimelineEntry }) => {
    const icon = CATEGORY_ICONS[event.event_type] ?? '📋'
    const label = CATEGORY_LABELS[event.event_type] ?? event.event_type
    const isAbnormal =
      event.summary?.toLowerCase().includes('abnormal') ||
      event.description?.toLowerCase().includes('abnormal')
    const riskLevel = event.risk_level as RiskLevel | null

    return (
      <YStack
        bg="$backgroundHover"
        br="$4"
        p="$3"
        gap="$2"
      >
        <XStack
          ai="center"
          gap="$2"
          fw="wrap"
        >
          <Text fontSize={18}>{icon}</Text>
          <YStack f={1}>
            <Text
              col="$color"
              fontWeight="600"
              size="$4"
            >
              {event.title}
            </Text>
            <Paragraph
              col="$colorSubdued"
              size="$2"
            >
              {label}
            </Paragraph>
          </YStack>
          {isAbnormal ? (
            <YStack
              bg="$red5"
              br="$2"
              px="$2"
              py="$1"
            >
              <Text
                col="$red10"
                size="$2"
                fontWeight="600"
              >
                ABNORMAL
              </Text>
            </YStack>
          ) : null}
          {riskLevel ? <RiskBadge level={riskLevel} /> : null}
        </XStack>

        <Text
          col="$color"
          size="$4"
        >
          {event.summary}
        </Text>

        <XStack
          ai="center"
          gap="$2"
        >
          <SourceBadge
            source={event.source}
            confidence={event.confidence != null ? Math.round(event.confidence * 100) : undefined}
          />
        </XStack>

        {typeof event.source_display === 'string' && event.source_display.length > 0 ? (
          <Paragraph
            col="$colorSubdued"
            size="$2"
            o={0.5}
          >
            {event.source_display}
          </Paragraph>
        ) : null}
      </YStack>
    )
  }

  return (
    <YStack
      f={1}
      bg="$background"
    >
      <YStack
        px="$4"
        pt="$4"
        pb="$2"
      >
        <H2
          col="$color"
          size="$7"
        >
          Health Timeline
        </H2>
        <Paragraph
          col="$colorSubdued"
          size="$3"
        >
          Your clinical events, consent-gated and de-identified.
        </Paragraph>
      </YStack>

      <SectionList
        style={{ flex: 1 }}
        sections={sections}
        keyExtractor={(item) => item.event_id}
        stickySectionHeadersEnabled={false}
        showsVerticalScrollIndicator={false}
        renderItem={renderTimelineItem}
        renderSectionHeader={({ section }) => (
          <XStack
            ai="center"
            gap="$3"
            py="$2"
          >
            <Separator f={1} />
            <Text
              col="$colorSubdued"
              size="$2"
              fontWeight="600"
              textTransform="uppercase"
              letterSpacing={1}
            >
              {section.title}
            </Text>
            <Separator f={1} />
          </XStack>
        )}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => void fetchTimeline('refresh')}
          />
        }
        contentContainerStyle={{
          flexGrow: sections.length === 0 ? 1 : 0,
          paddingHorizontal: 16,
          paddingTop: 12,
          paddingBottom: insets.bottom + 96,
          gap: 12,
        }}
        ListHeaderComponent={
          error !== null && timeline.length > 0 ? (
            <XStack
              ai="center"
              gap="$2"
              pb="$2"
            >
              <Text>⚠️</Text>
              <Paragraph
                f={1}
                col="$red10"
                size="$2"
              >
                {error}
              </Paragraph>
              <Button
                size="$2"
                chromeless
                onPress={() => void fetchTimeline('refresh')}
              >
                Retry
              </Button>
            </XStack>
          ) : null
        }
        ListEmptyComponent={
          <YStack
            f={1}
            ai="center"
            jc="center"
            py="$8"
            gap="$2"
          >
            {initialLoading ? (
              <>
                <Spinner
                  size="large"
                  color="$blue10"
                />
                <Paragraph
                  col="$colorSubdued"
                  size="$4"
                >
                  Loading timeline…
                </Paragraph>
              </>
            ) : error !== null ? (
              <>
                <Text fontSize={36}>⚠️</Text>
                <Paragraph
                  col="$red10"
                  size="$4"
                  ta="center"
                >
                  {error}
                </Paragraph>
                <Button
                  size="$3"
                  chromeless
                  onPress={() => void fetchTimeline('initial')}
                >
                  Retry
                </Button>
              </>
            ) : (
              <>
                <Text fontSize={48}>📊</Text>
                <Paragraph
                  col="$colorSubdued"
                  size="$4"
                  ta="center"
                >
                  No clinical events yet.
                </Paragraph>
                <Paragraph
                  col="$colorSubdued"
                  size="$3"
                  ta="center"
                  o={0.6}
                >
                  When your provider adds clinical information, it will appear here.
                </Paragraph>
              </>
            )}
          </YStack>
        }
      />

      <YStack
        px="$4"
        pt="$2"
        pb={insets.bottom + 12}
        borderTopWidth={1}
        borderTopColor="$borderColor"
        bg="$background"
      >
        <Button
          chromeless
          size="$3"
          onPress={() => router.push('/patient/access-history')}
        >
          ← Access History
        </Button>
      </YStack>
    </YStack>
  )
}
