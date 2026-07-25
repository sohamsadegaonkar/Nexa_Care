import { useRouter } from 'expo-router'
import {
  YStack,
  H2,
  Paragraph,
  Text,
  ScrollView,
  XStack,
  Separator,
  Button,
  Spinner,
} from 'tamagui'
import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../../utils/apiClient'

/**
 * Access history screen — who accessed the patient's data and when.
 *
 * Fetches via apiClient.get('/api/v2/patient/me/access-history').
 * Backend: GET /api/v2/patient/me/access-history
 * Response: { patient_id, access_history: AccessHistoryEntry[] }
 *
 * Each access shows doctor name, hospital, purpose, timestamp, and
 * data categories accessed.  Break-glass / emergency accesses are
 * shown with a distinct red ⚠️ BREAK-GLASS warning badge.
 */

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

const FLAG_ICONS: Record<string, string> = {
  BREAK_GLASS_ACCESS: '🚨',
  ROUTINE_ACCESS: '👁️',
}

export default function AccessHistoryScreen({ history: initialHistory }: AccessHistoryScreenProps) {
  const router = useRouter()
  const [history, setHistory] = useState<AccessHistoryEntry[]>(initialHistory ?? [])
  const [loading, setLoading] = useState(!initialHistory)
  const [error, setError] = useState<string | null>(null)

  const fetchHistory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const { data } = await apiClient.get('/api/v2/patient/me/access-history')
      setHistory(data?.access_history ?? [])
    } catch {
      setError('Failed to load access history.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (initialHistory) return
    fetchHistory()
  }, [initialHistory, fetchHistory])

  const handleRetry = () => {
    fetchHistory()
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
          Access History
        </H2>
        <Paragraph
          col="$colorSubdued"
          size="$3"
        >
          Every time a provider accesses your data, it's recorded here.
        </Paragraph>
      </YStack>

      <ScrollView
        f={1}
        bg="$background"
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{
          flexGrow: 1,
          padding: 16,
          paddingBottom: 24,
          gap: 8,
        }}
      >
        {/* Loading state */}
        {loading && (
          <YStack
            f={1}
            ai="center"
            jc="center"
            py="$8"
            gap="$3"
          >
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
          </YStack>
        )}

        {/* Error state */}
        {error !== null ? (
          <YStack
            f={1}
            ai="center"
            jc="center"
            py="$8"
            gap="$2"
          >
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
              onPress={handleRetry}
            >
              Retry
            </Button>
          </YStack>
        ) : null}

        {/* Empty state */}
        {!loading && error === null && history.length === 0 ? (
          <YStack
            f={1}
            ai="center"
            jc="center"
            py="$8"
            gap="$2"
          >
            <Text fontSize={48}>📭</Text>
            <Paragraph
              col="$colorSubdued"
              size="$4"
              ta="center"
            >
              No one has accessed your records yet.
            </Paragraph>
            <Paragraph
              col="$colorSubdued"
              size="$3"
              ta="center"
              o={0.6}
            >
              When a provider requests or accesses your data, it will appear here.
            </Paragraph>
          </YStack>
        ) : null}

        {/* Access entries */}
        {!loading && error === null
          ? history.map((entry) => (
              <YStack
                key={entry.audit_id}
                bg="$backgroundHover"
                br="$4"
                p="$3"
                gap="$2"
              >
                {/* Header: icon + event type + break-glass badge + timestamp */}
                <XStack
                  ai="center"
                  gap="$2"
                  fw="wrap"
                >
                  <Text fontSize={16}>
                    {entry.is_break_glass
                      ? FLAG_ICONS.BREAK_GLASS_ACCESS
                      : FLAG_ICONS.ROUTINE_ACCESS}
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

                {/* Doctor + hospital */}
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

                {/* Purpose */}
                <Paragraph
                  col="$colorSubdued"
                  size="$2"
                  o={0.8}
                >
                  Purpose: {entry.purpose}
                </Paragraph>

                {/* Data categories */}
                {Array.isArray(entry.data_categories) && entry.data_categories.length > 0 ? (
                  <XStack
                    fw="wrap"
                    gap="$1"
                    mt="$1"
                  >
                    {entry.data_categories.map((cat) => (
                      <YStack
                        key={cat}
                        bg="$backgroundFocus"
                        br="$2"
                        px="$2"
                        py="$1"
                      >
                        <Text
                          col="$colorSubdued"
                          size="$1"
                        >
                          {cat}
                        </Text>
                      </YStack>
                    ))}
                  </XStack>
                ) : null}
              </YStack>
            ))
          : null}
      </ScrollView>

      <YStack
        p="$4"
        gap="$3"
        bg="$background"
        borderTopWidth={1}
        borderTopColor="$borderColor"
      >
        <Separator />
        <Button
          f={1}
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

function formatTimestamp(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / (1000 * 60))
  const diffHr = Math.floor(diffMs / (1000 * 60 * 60))
  const diffDay = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  if (diffMin < 1) return 'Just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffHr < 24) return `${diffHr}h ago`
  if (diffDay < 7) return `${diffDay}d ago`
  return d.toLocaleDateString()
}
