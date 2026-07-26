import { useFocusEffect, useRouter } from 'expo-router'
import { Button, H2, Paragraph, Spinner, Text, XStack, YStack } from 'tamagui'
import { useCallback, useState } from 'react'
import { FlatList } from 'react-native'
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

const FLAG_ICONS: Record<string, string> = {
  BREAK_GLASS_ACCESS: '🚨',
  ROUTINE_ACCESS: '👁️',
}

export default function AccessHistoryScreen({ history: initialHistory }: AccessHistoryScreenProps) {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [history, setHistory] = useState<AccessHistoryEntry[]>(initialHistory ?? [])
  const [loading, setLoading] = useState(!initialHistory)
  const [loadingMore, setLoadingMore] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchHistory = useCallback(async (cursor?: string) => {
    if (cursor) setLoadingMore(true)
    else setLoading(true)
    setError(null)

    try {
      const path = cursor
        ? `/api/v2/patient/me/access-history?cursor=${encodeURIComponent(cursor)}`
        : '/api/v2/patient/me/access-history'
      const response = await apiClient.get<AccessHistoryResponse>(path)
      const payload =
        response?.data ?? (response as unknown as AccessHistoryResponse | null | undefined)
      const entries = Array.isArray(payload?.access_history) ? payload.access_history : []
      setHistory((current) => {
        if (!cursor) return entries
        const existingIds = new Set(current.map((entry) => entry.audit_id))
        return [...current, ...entries.filter((entry) => !existingIds.has(entry.audit_id))]
      })
      setNextCursor(typeof payload?.next_cursor === 'string' ? payload.next_cursor : null)
    } catch {
      setError('Failed to load access history.')
    } finally {
      if (cursor) setLoadingMore(false)
      else setLoading(false)
    }
  }, [])

  useFocusEffect(
    useCallback(() => {
      void fetchHistory()
    }, [fetchHistory])
  )

  const renderHistoryItem = ({ item: entry }: { item: AccessHistoryEntry }) => (
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
        <Text fontSize={16}>
          {entry.is_break_glass ? FLAG_ICONS.BREAK_GLASS_ACCESS : FLAG_ICONS.ROUTINE_ACCESS}
        </Text>
        <Text
          col="$color"
          fontWeight="600"
          size="$4"
        >
          {entry.is_break_glass ? 'Emergency Access' : 'Data Accessed'}
        </Text>
        {entry.is_break_glass ? (
          <XStack
            bg="$red5"
            br="$2"
            px="$2"
            py="$1"
            ai="center"
            gap="$1"
          >
            <Text size="$1">⚠️</Text>
            <Text
              col="$red10"
              size="$1"
              fontWeight="700"
            >
              BREAK-GLASS
            </Text>
          </XStack>
        ) : null}
        <Text
          col="$colorSubdued"
          size="$2"
          ml="auto"
        >
          {formatTimestamp(entry.accessed_at)}
        </Text>
      </XStack>

      <XStack
        ai="center"
        gap="$2"
        fw="wrap"
      >
        {entry.doctor_name ? (
          <Paragraph
            col="$color"
            size="$3"
            fontWeight="600"
          >
            {entry.doctor_name}
          </Paragraph>
        ) : null}
        {entry.doctor_name && entry.hospital_name ? (
          <Text
            col="$colorSubdued"
            size="$2"
            o={0.4}
          >
            •
          </Text>
        ) : null}
        {entry.hospital_name ? (
          <Paragraph
            col="$colorSubdued"
            size="$3"
          >
            {entry.hospital_name}
          </Paragraph>
        ) : null}
      </XStack>

      <Paragraph
        col="$colorSubdued"
        size="$2"
        o={0.8}
      >
        Purpose: {entry.purpose}
      </Paragraph>

      {Array.isArray(entry.data_categories) && entry.data_categories.length > 0 ? (
        <XStack
          fw="wrap"
          gap="$1"
          mt="$1"
        >
          {entry.data_categories.map((category) => (
            <YStack
              key={category}
              bg="$backgroundFocus"
              br="$2"
              px="$2"
              py="$1"
            >
              <Text
                col="$colorSubdued"
                size="$1"
              >
                {category}
              </Text>
            </YStack>
          ))}
        </XStack>
      ) : null}
    </YStack>
  )

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
          Access History
        </H2>
        <Paragraph
          col="$colorSubdued"
          size="$3"
        >
          Every time a provider accesses your data, it's recorded here.
        </Paragraph>
      </YStack>

      <FlatList
        style={{ flex: 1 }}
        data={!loading && error === null ? history : []}
        keyExtractor={(item) => item.audit_id}
        renderItem={renderHistoryItem}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{
          flexGrow: history.length === 0 ? 1 : undefined,
          padding: 16,
          paddingBottom: insets.bottom + 32,
          gap: 12,
        }}
        ListEmptyComponent={
          <YStack
            f={1}
            ai="center"
            jc="center"
            py="$8"
            gap="$3"
          >
            {loading ? (
              <>
                <Spinner
                  size="large"
                  color="$blue10"
                />
                <Paragraph
                  col="$colorSubdued"
                  size="$4"
                >
                  Loading history...
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
                  onPress={() => fetchHistory()}
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
          !loading && error === null ? (
            <YStack
              gap="$3"
              pt="$3"
            >
              {nextCursor ? (
                <Button
                  size="$3"
                  chromeless
                  disabled={loadingMore}
                  onPress={() => fetchHistory(nextCursor)}
                >
                  {loadingMore ? 'Loading older records...' : 'Load older records'}
                </Button>
              ) : null}
              <Button
                theme="blue"
                size="$3"
                onPress={() => router.push('/patient/timeline')}
              >
                View Health Timeline
              </Button>
            </YStack>
          ) : null
        }
      />
    </YStack>
  )
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  const diffMs = Date.now() - date.getTime()
  const diffMin = Math.floor(diffMs / (1000 * 60))
  const diffHr = Math.floor(diffMs / (1000 * 60 * 60))
  const diffDay = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  if (diffMin < 1) return 'Just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffHr < 24) return `${diffHr}h ago`
  if (diffDay < 7) return `${diffDay}d ago`
  return date.toLocaleDateString()
}
