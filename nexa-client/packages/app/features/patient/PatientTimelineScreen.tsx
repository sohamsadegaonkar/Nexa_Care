import { useRouter } from 'expo-router'
import { Button, H2, Paragraph, Separator, Spinner, Text, XStack, YStack } from 'tamagui'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { RefreshControl, SectionList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { apiClient } from '../../utils/apiClient'
import SourceBadge from './badges/SourceBadge'
import RiskBadge, { type RiskLevel } from './badges/RiskBadge'
import PatientRecordDetailModal from './PatientRecordDetailModal'

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
  hospital_name?: string | null
  confidence?: number | null
  risk_level?: string | null
  record_id?: string | null
  category?: string | null
  has_source_document?: boolean
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

const FILTER_CATEGORIES = [
  { key: 'ALL', label: 'All Events' },
  { key: 'VITALS', label: 'Vitals' },
  { key: 'MEDICATION', label: 'Medications' },
  { key: 'LAB_RESULT', label: 'Labs' },
  { key: 'DOCUMENT', label: 'Documents' },
  { key: 'ALLERGY', label: 'Allergies' },
]

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
      return {
        events: direct.events,
        next_cursor: direct.next_cursor ?? null,
      }
    }

    const data = (response as { data?: unknown }).data
    if (data && typeof data === 'object') {
      const wrapped = data as Partial<TimelineResponse>
      if (Array.isArray(wrapped.events)) {
        return {
          events: wrapped.events,
          next_cursor: wrapped.next_cursor ?? null,
        }
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
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [activeFilter, setActiveFilter] = useState<string>('ALL')
  const [error, setError] = useState<string | null>(null)
  const [selectedEvent, setSelectedEvent] = useState<TimelineEntry | null>(null)
  const timelineRef = useRef(initialTimeline ?? [])
  const activeRequestIdRef = useRef(0)
  const loadingOlderRef = useRef(false)
  const activeFilterRef = useRef(activeFilter)
  activeFilterRef.current = activeFilter

  const fetchTimeline = useCallback(
    async (mode: 'initial' | 'refresh' | 'append', cursor?: string | null, filterKey?: string) => {
      if (mode === 'append' && loadingOlderRef.current) return
      const currentRequestId = ++activeRequestIdRef.current

      if (mode === 'initial') {
        if (timelineRef.current.length === 0) setInitialLoading(true)
      } else if (mode === 'refresh') {
        setRefreshing(true)
      } else if (mode === 'append') {
        loadingOlderRef.current = true
        setLoadingOlder(true)
      }
      setError(null)

      try {
        const params = new URLSearchParams()
        params.set('limit', '20')
        if (cursor) {
          params.set('cursor', cursor)
        }
        const effectiveFilter = filterKey !== undefined ? filterKey : activeFilterRef.current
        if (effectiveFilter && effectiveFilter !== 'ALL') {
          // Map to backend category name
          let catParam = effectiveFilter.toLowerCase()
          if (catParam === 'lab_result') catParam = 'labs'
          else if (catParam === 'medication') catParam = 'medications'
          else if (catParam === 'allergy') catParam = 'allergies'
          else if (catParam === 'document') catParam = 'documents'
          params.set('category', catParam)
        }

        const qs = params.toString()
        const url = `/api/v2/patient/me/timeline${qs ? `?${qs}` : ''}`
        const response = (await apiClient.get(url)) as unknown
        const payload = normalizeTimelineResponse(response)

        if (currentRequestId !== activeRequestIdRef.current) {
          return // stale response superseded by newer filter request
        }

        if (mode === 'append') {
          const merged = [...timelineRef.current, ...payload.events]
          timelineRef.current = merged
          setTimeline(merged)
        } else {
          timelineRef.current = payload.events
          setTimeline(payload.events)
        }
        setNextCursor(payload.next_cursor)
        setError(null)
      } catch (caught) {
        if (currentRequestId !== activeRequestIdRef.current) {
          return
        }
        setError(
          caught instanceof Error && caught.message === 'INVALID_TIMELINE_RESPONSE'
            ? 'Health timeline returned an invalid response.'
            : 'Failed to load health timeline.'
        )
      } finally {
        if (currentRequestId === activeRequestIdRef.current) {
          loadingOlderRef.current = false
          setInitialLoading(false)
          setRefreshing(false)
          setLoadingOlder(false)
        }
      }
    },
    []
  )

  useEffect(() => {
    if (initialTimeline !== undefined) return
    void fetchTimeline('initial')
  }, [initialTimeline, fetchTimeline])

  const handleFilterChange = (filterKey: string) => {
    setActiveFilter(filterKey)
    setNextCursor(null)
    if (initialTimeline !== undefined) {
      if (filterKey === 'ALL') {
        setTimeline(initialTimeline)
      } else {
        setTimeline(initialTimeline.filter((ev) => ev.event_type === filterKey))
      }
      return
    }
    void fetchTimeline('initial', null, filterKey)
  }

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
        pressStyle={{ opacity: 0.85 }}
        onPress={() => setSelectedEvent(event)}
        accessibilityRole="button"
        accessibilityLabel={`View details for ${event.title}`}
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
          justifyContent="space-between"
          flexWrap="wrap"
        >
          <SourceBadge
            source={event.source}
            confidence={event.confidence != null ? Math.round(event.confidence * 100) : undefined}
            hospitalName={event.hospital_name || undefined}
          />
          <Text color="$color10" fontSize="$2">
            View Details →
          </Text>
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
        gap="$2"
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

        {/* Category Filter Pills */}
        <XStack
          gap="$2"
          flexWrap="wrap"
          paddingTop="$1"
        >
          {FILTER_CATEGORIES.map((cat) => {
            const isActive = activeFilter === cat.key
            return (
              <Button
                key={cat.key}
                size="$2"
                backgroundColor={isActive ? '$blue9' : '$backgroundHover'}
                color={isActive ? 'white' : '$color'}
                borderRadius="$3"
                onPress={() => handleFilterChange(cat.key)}
                accessibilityRole="button"
                accessibilityLabel={`Filter timeline by ${cat.label}`}
                accessibilityState={{ selected: isActive }}
              >
                {cat.label}
              </Button>
            )
          })}
        </XStack>
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
            ) : activeFilter !== 'ALL' ? (
              <>
                <Text fontSize={48}>🔍</Text>
                <Paragraph
                  color="$color10"
                  size="$4"
                  textAlign="center"
                >
                  {`No ${CATEGORY_LABELS[activeFilter] ?? activeFilter} events found for this filter.`}
                </Paragraph>
                <Paragraph
                  color="$color10"
                  size="$3"
                  textAlign="center"
                  opacity={0.6}
                >
                  Try selecting another category or view all clinical events.
                </Paragraph>
                <Button
                  size="$3"
                  theme="blue"
                  marginTop="$2"
                  onPress={() => handleFilterChange('ALL')}
                  accessibilityRole="button"
                  accessibilityLabel="Show all clinical events"
                >
                  Show All Events
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
        ListFooterComponent={
          nextCursor ? (
            <YStack
              paddingVertical="$3"
              alignItems="center"
              justifyContent="center"
            >
              <Button
                size="$3"
                theme="blue"
                disabled={loadingOlder}
                onPress={() => void fetchTimeline('append', nextCursor)}
                accessibilityRole="button"
                accessibilityLabel="Load older timeline events"
              >
                {loadingOlder ? (
                  <XStack gap="$2" alignItems="center">
                    <Spinner size="small" color="white" />
                    <Text color="white">Loading older events…</Text>
                  </XStack>
                ) : (
                  'Load older timeline events'
                )}
              </Button>
            </YStack>
          ) : timeline.length > 0 ? (
            <YStack
              paddingVertical="$4"
              alignItems="center"
              justifyContent="center"
            >
              <Paragraph
                color="$color10"
                size="$2"
                opacity={0.6}
              >
                ✓ All timeline events loaded
              </Paragraph>
            </YStack>
          ) : null
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

      {/* Record Detail Modal */}
      {selectedEvent !== null && (
        <PatientRecordDetailModal
          open={true}
          onOpenChange={(open) => {
            if (!open) setSelectedEvent(null)
          }}
          category={
            selectedEvent.category ||
            (selectedEvent.event_type ? selectedEvent.event_type.toLowerCase() : 'vitals')
          }
          recordId={selectedEvent.record_id || selectedEvent.event_id || null}
          initialTitle={selectedEvent.title}
          initialFields={{
            Summary: selectedEvent.summary,
            EventType: selectedEvent.event_type,
            Date: selectedEvent.occurred_at || selectedEvent.event_date,
          }}
          initialProvenance={{
            source: selectedEvent.source,
            source_display: selectedEvent.source_display,
            hospital_name: selectedEvent.hospital_name,
            confidence: selectedEvent.confidence,
            risk_level: selectedEvent.risk_level,
            has_source_document: selectedEvent.has_source_document,
          }}
          initialRecordedAt={selectedEvent.occurred_at || selectedEvent.event_date}
        />
      )}
    </YStack>
  )
}
