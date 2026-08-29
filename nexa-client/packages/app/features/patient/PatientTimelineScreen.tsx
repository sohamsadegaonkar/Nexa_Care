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
        backgroundColor="$backgroundHover"
        borderRadius="$4"
        padding="$3"
        gap="$2"
      >
        <XStack
          alignItems="center"
          gap="$2"
          flexWrap="wrap"
        >
          <Text fontSize={18}>{icon}</Text>
          <YStack flex={1}>
            <Text
              color="$color"
              fontWeight="600"
              fontSize="$4"
            >
              {event.title}
            </Text>
            <Paragraph
              color="$color10"
              size="$2"
            >
              {label}
            </Paragraph>
          </YStack>
          {isAbnormal ? (
            <YStack
              backgroundColor="$red5"
              borderRadius="$2"
              paddingHorizontal="$2"
              paddingVertical="$1"
            >
              <Text
                color="$red10"
                fontSize="$2"
                fontWeight="600"
              >
                ABNORMAL
              </Text>
            </YStack>
          ) : null}
          {riskLevel ? <RiskBadge level={riskLevel} /> : null}
        </XStack>

        <Text
          color="$color"
          fontSize="$4"
        >
          {event.summary}
        </Text>

        <XStack
          alignItems="center"
          gap="$2"
        >
          <SourceBadge
            source={event.source === 'manual' ? 'manual' : 'ai_extracted'}
            confidence={event.confidence != null ? Math.round(event.confidence * 100) : undefined}
          />
        </XStack>

        {typeof event.source_display === 'string' && event.source_display.length > 0 ? (
          <Paragraph
            color="$color10"
            size="$2"
            opacity={0.5}
          >
            {event.source_display}
          </Paragraph>
        ) : null}
      </YStack>
    )
  }

  return (
    <YStack
      flex={1}
      backgroundColor="$background"
    >
      <YStack
        paddingHorizontal="$4"
        paddingTop="$4"
        paddingBottom="$2"
      >
        <H2
          color="$color"
          size="$7"
        >
          Health Timeline
        </H2>
        <Paragraph
          color="$color10"
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
            alignItems="center"
            gap="$3"
            paddingVertical="$2"
          >
            <Separator flex={1} />
            <Text
              color="$color10"
              fontSize="$2"
              fontWeight="600"
              textTransform="uppercase"
              letterSpacing={1}
            >
              {section.title}
            </Text>
            <Separator flex={1} />
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
              alignItems="center"
              gap="$2"
              paddingBottom="$2"
            >
              <Text>⚠️</Text>
              <Paragraph
                flex={1}
                color="$red10"
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
            flex={1}
            alignItems="center"
            justifyContent="center"
            paddingVertical="$8"
            gap="$2"
          >
            {initialLoading ? (
              <>
                <Spinner
                  size="large"
                  color="$blue10"
                />
                <Paragraph
                  color="$color10"
                  size="$4"
                >
                  Loading timeline…
                </Paragraph>
              </>
            ) : error !== null ? (
              <>
                <Text fontSize={36}>⚠️</Text>
                <Paragraph
                  color="$red10"
                  size="$4"
                  textAlign="center"
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
                  color="$color10"
                  size="$4"
                  textAlign="center"
                >
                  No clinical events yet.
                </Paragraph>
                <Paragraph
                  color="$color10"
                  size="$3"
                  textAlign="center"
                  opacity={0.6}
                >
                  When your provider adds clinical information, it will appear here.
                </Paragraph>
              </>
            )}
          </YStack>
        }
      />

      <YStack
        paddingHorizontal="$4"
        paddingTop="$2"
        paddingBottom={insets.bottom + 12}
        borderTopWidth={1}
        borderTopColor="$borderColor"
        backgroundColor="$background"
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
