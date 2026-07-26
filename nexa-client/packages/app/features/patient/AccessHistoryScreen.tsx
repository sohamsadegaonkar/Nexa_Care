import { useFocusEffect, useRouter } from 'expo-router'
import { Button, H2, Paragraph, Spinner, Text, XStack, YStack } from 'tamagui'
import { useCallback, useRef, useState } from 'react'
import { FlatList, RefreshControl, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { apiClient } from '../../utils/apiClient'

interface AccessHistoryEntry {
  audit_id: string
  accessed_by?: string
  doctor_name: string
  hospital_name: string
  purpose: string
  accessed_at: string
  data_categories: string[]
  is_break_glass: boolean
  flag: 'BREAK_GLASS_ACCESS' | 'ROUTINE_ACCESS'
  event_type?: string
}

interface AccessHistoryScreenProps {
  history?: AccessHistoryEntry[]
}

interface AccessHistoryResponse {
  access_history: AccessHistoryEntry[]
  next_cursor: string | null
}

type FetchMode = 'refresh' | 'append'

function normalizeAccessHistoryResponse(response: unknown): AccessHistoryResponse {
  if (response && typeof response === 'object') {
    const direct = response as Partial<AccessHistoryResponse>
    if (Array.isArray(direct.access_history)) {
      return direct as AccessHistoryResponse
    }

    const data = (response as { data?: unknown }).data
    if (data && typeof data === 'object') {
      const wrapped = data as Partial<AccessHistoryResponse>
      if (Array.isArray(wrapped.access_history)) {
        return wrapped as AccessHistoryResponse
      }
    }
  }

  throw new Error('INVALID_ACCESS_HISTORY_RESPONSE')
}

const HUMANIZED_VALUES: Record<string, string> = {
  treatment: 'Treatment',
  diagnostic_review: 'Diagnostic review',
  follow_up: 'Follow-up',
  emergency_care: 'Emergency care',
  clinical_summary: 'Clinical summary',
  lab_results: 'Lab results',
}

function humanizeValue(value: string): string {
  const normalized = value.trim().toLowerCase()
  if (!normalized) return 'Not recorded'
  return (
    HUMANIZED_VALUES[normalized] ??
    normalized.replace(/_/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase())
  )
}

function formatExactTimestamp(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Time unavailable'

  const datePart = new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date)
  const timePart = new Intl.DateTimeFormat('en-IN', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
    .format(date)
    .replace(/\b(am|pm)\b/i, (period) => period.toUpperCase())
  return `${datePart} · ${timePart}`
}

function formatRelativeTimestamp(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Time unavailable'

  const differenceMs = Date.now() - date.getTime()
  if (differenceMs < 0) return 'Just now'

  const minutes = Math.floor(differenceMs / 60_000)
  const hours = Math.floor(differenceMs / 3_600_000)
  const days = Math.floor(differenceMs / 86_400_000)

  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes} min ago`
  if (hours < 24) return `${hours} hr ago`
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`
  return formatExactTimestamp(value)
}

export default function AccessHistoryScreen({ history: initialHistory }: AccessHistoryScreenProps) {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [history, setHistory] = useState<AccessHistoryEntry[]>(initialHistory ?? [])
  const [initialLoading, setInitialLoading] = useState(initialHistory === undefined)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const historyRef = useRef(initialHistory ?? [])
  const requestInFlightRef = useRef(false)

  const fetchHistory = useCallback(
    async ({ mode, cursor }: { mode: FetchMode; cursor?: string }) => {
      if (requestInFlightRef.current) return
      requestInFlightRef.current = true
      const hasExistingHistory = historyRef.current.length > 0
      if (mode === 'append') setLoadingMore(true)
      else if (hasExistingHistory) setRefreshing(true)
      else setInitialLoading(true)
      setError(null)

      try {
        const path =
          mode === 'append' && cursor
            ? `/api/v2/patient/me/access-history?cursor=${encodeURIComponent(cursor)}`
            : '/api/v2/patient/me/access-history'
        const response = (await apiClient.get<AccessHistoryResponse>(path)) as unknown
        const payload = normalizeAccessHistoryResponse(response)
        const entries = payload.access_history
        if (
          entries.some(
            (entry) =>
              !entry ||
              typeof entry !== 'object' ||
              typeof entry.audit_id !== 'string' ||
              !entry.audit_id.trim()
          )
        ) {
          throw new Error('INVALID_ACCESS_HISTORY_RESPONSE')
        }

        let nextHistory = entries
        if (mode === 'append') {
          const existingIds = new Set(historyRef.current.map((entry) => entry.audit_id))
          nextHistory = [
            ...historyRef.current,
            ...entries.filter((entry) => !existingIds.has(entry.audit_id)),
          ]
        }
        historyRef.current = nextHistory
        setHistory(nextHistory)
        setNextCursor(typeof payload.next_cursor === 'string' ? payload.next_cursor : null)
        setError(null)
      } catch (caught) {
        setError(
          caught instanceof Error && caught.message === 'INVALID_ACCESS_HISTORY_RESPONSE'
            ? 'Access history returned an invalid response.'
            : 'Failed to load access history.'
        )
      } finally {
        requestInFlightRef.current = false
        setInitialLoading(false)
        setRefreshing(false)
        setLoadingMore(false)
      }
    },
    []
  )

  useFocusEffect(
    useCallback(() => {
      void fetchHistory({ mode: 'refresh' })
    }, [fetchHistory])
  )

  const renderHistoryItem = ({ item }: { item: AccessHistoryEntry }) => {
    const accessLabel = item.is_break_glass ? 'Emergency access' : 'Routine access'
    const doctorName = item.doctor_name?.trim() || 'Former or unavailable provider'
    const hospitalName = item.hospital_name?.trim() || 'Unknown facility'
    const purpose = humanizeValue(item.purpose || '')
    const exactTimestamp = formatExactTimestamp(item.accessed_at)
    const relativeTimestamp = formatRelativeTimestamp(item.accessed_at)

    return (
      <View
        style={{
          marginHorizontal: 16,
          marginVertical: 6,
        }}
        collapsable={false}
        accessible
        accessibilityLabel={`${accessLabel} by ${doctorName} at ${hospitalName} on ${exactTimestamp} for ${purpose}.`}
      >
        <YStack
          minHeight={168}
          backgroundColor="$backgroundHover"
          borderRadius="$4"
          borderWidth={1}
          borderColor="$borderColor"
          borderLeftWidth={4}
          borderLeftColor={item.is_break_glass ? '$red9' : '$blue9'}
          padding="$4"
          gap="$3"
        >
          <XStack
            alignItems="center"
            flexWrap="wrap"
            gap="$2"
          >
            <Text fontSize={18}>{item.is_break_glass ? '🚨' : '🛡️'}</Text>
            <Text
              color="$color"
              fontWeight="700"
              size="$4"
            >
              {accessLabel}
            </Text>
            <YStack
              backgroundColor={item.is_break_glass ? '$red5' : '$blue5'}
              borderRadius="$2"
              paddingHorizontal="$2"
              paddingVertical="$1"
            >
              <Text
                color={item.is_break_glass ? '$red11' : '$blue11'}
                fontWeight="700"
                size="$1"
              >
                {item.is_break_glass ? 'EMERGENCY ACCESS' : 'ROUTINE'}
              </Text>
            </YStack>
            <Text
              color="$colorSubdued"
              marginLeft="auto"
              size="$2"
            >
              {relativeTimestamp}
            </Text>
          </XStack>

          <YStack gap="$1">
            <Text
              color="$color"
              fontWeight="600"
              size="$4"
            >
              {doctorName}
            </Text>
            <Paragraph
              color="$colorSubdued"
              size="$3"
            >
              {hospitalName}
            </Paragraph>
          </YStack>

          <YStack gap="$1">
            <Text
              color="$colorSubdued"
              fontWeight="600"
              size="$2"
            >
              Purpose
            </Text>
            <Text
              color="$color"
              size="$3"
            >
              {purpose}
            </Text>
          </YStack>

          {Array.isArray(item.data_categories) && item.data_categories.length > 0 ? (
            <XStack
              flexWrap="wrap"
              gap="$2"
            >
              {item.data_categories.map((category, index) => (
                <YStack
                  key={`${category}-${index}`}
                  backgroundColor="$backgroundFocus"
                  borderRadius="$3"
                  paddingHorizontal="$2"
                  paddingVertical="$1"
                >
                  <Text
                    color="$colorSubdued"
                    size="$2"
                  >
                    {humanizeValue(category)}
                  </Text>
                </YStack>
              ))}
            </XStack>
          ) : null}

          <XStack
            alignItems="center"
            gap="$2"
          >
            <Text fontSize={14}>🕒</Text>
            <Text
              color="$colorSubdued"
              size="$2"
            >
              {exactTimestamp}
            </Text>
          </XStack>
        </YStack>
      </View>
    )
  }

  return (
    <YStack
      flex={1}
      backgroundColor="$background"
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
          Access History
        </H2>
        <Paragraph
          col="$colorSubdued"
          size="$3"
        >
          Every time a provider accesses your data, it's recorded here.
        </Paragraph>
      </YStack>

      <View style={{ flex: 1 }}>
        <FlatList
          style={{ flex: 1 }}
          data={history}
          keyExtractor={(item) => item.audit_id.trim()}
          renderItem={renderHistoryItem}
          removeClippedSubviews={false}
          initialNumToRender={20}
          windowSize={5}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void fetchHistory({ mode: 'refresh' })}
            />
          }
          contentContainerStyle={{
            flexGrow: history.length === 0 ? 1 : 0,
            paddingBottom: insets.bottom + 24,
          }}
          ListHeaderComponent={
            error !== null && history.length > 0 ? (
              <XStack
                ai="center"
                gap="$2"
                px="$4"
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
                  onPress={() => void fetchHistory({ mode: 'refresh' })}
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
              px="$4"
              gap="$3"
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
                    Loading access history…
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
                    onPress={() => void fetchHistory({ mode: 'refresh' })}
                  >
                    Retry
                  </Button>
                </>
              ) : (
                <>
                  <Text fontSize={48}>📭</Text>
                  <Paragraph
                    col="$colorSubdued"
                    size="$4"
                    ta="center"
                  >
                    No provider has accessed your records yet.
                  </Paragraph>
                  <Paragraph
                    col="$colorSubdued"
                    size="$3"
                    ta="center"
                    o={0.6}
                  >
                    When a provider accesses your data, it will appear here.
                  </Paragraph>
                </>
              )}
            </YStack>
          }
          ListFooterComponent={
            !initialLoading && error === null && nextCursor ? (
              <YStack
                gap="$3"
                px="$4"
                pt="$3"
              >
                <Button
                  size="$3"
                  chromeless
                  disabled={loadingMore}
                  onPress={() => void fetchHistory({ mode: 'append', cursor: nextCursor })}
                >
                  {loadingMore ? 'Loading older records…' : 'Load older records'}
                </Button>
              </YStack>
            ) : null
          }
        />
      </View>
      <YStack
        flexShrink={0}
        px="$4"
        pt="$2"
        pb={insets.bottom + 12}
      >
        <Button
          theme="blue"
          size="$3"
          onPress={() => router.push('/patient/timeline')}
        >
          View Health Timeline
        </Button>
      </YStack>
    </YStack>
  )
}
