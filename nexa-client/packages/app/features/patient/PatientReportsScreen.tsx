import { useRouter } from 'solito/navigation'
import {
  Button,
  H2,
  Paragraph,
  Separator,
  Spinner,
  Text,
  XStack,
  YStack,
} from 'tamagui'
import React, { useCallback, useEffect, useState } from 'react'
import { RefreshControl, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import {
  NexaApiClient,
  type PatientReportItem,
} from '../../utils/apiClient'
import PatientRecordDetailModal from './PatientRecordDetailModal'

export default function PatientReportsScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [reports, setReports] = useState<PatientReportItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedReport, setSelectedReport] = useState<PatientReportItem | null>(null)

  const [selectedTypeFilter, setSelectedTypeFilter] = useState<string>('all')
  const reportRequestIdRef = React.useRef(0)

  const loadReports = useCallback(
    async (cursor?: string | null, append = false, typeFilter?: string) => {
      const currentReqId = ++reportRequestIdRef.current
      if (append) {
        setLoadingOlder(true)
      } else {
        if (!cursor) {
          setLoading(true)
          setReports([])
          setNextCursor(null)
          setLoadingOlder(false)
        }
      }
      setError(null)

      const activeFilter = typeFilter !== undefined ? typeFilter : selectedTypeFilter

      try {
        const res = await NexaApiClient.getMyReports({
          cursor,
          limit: 20,
          documentType: activeFilter !== 'all' ? activeFilter : null,
        })
        if (currentReqId !== reportRequestIdRef.current) return
        if (append) {
          setReports((prev) => [...prev, ...res.reports])
        } else {
          setReports(res.reports)
        }
        setNextCursor(res.next_cursor)
      } catch (err) {
        if (currentReqId !== reportRequestIdRef.current) return
        setError(
          err instanceof Error
            ? err.message
            : 'Failed to load medical reports'
        )
      } finally {
        if (currentReqId === reportRequestIdRef.current) {
          setLoading(false)
          setRefreshing(false)
          setLoadingOlder(false)
        }
      }
    },
    [selectedTypeFilter]
  )

  useEffect(() => {
    void loadReports()
  }, [loadReports])

  const handleSelectFilter = (filterKey: string) => {
    setSelectedTypeFilter(filterKey)
    setReports([])
    setNextCursor(null)
    setLoadingOlder(false)
    setError(null)
    void loadReports(null, false, filterKey)
  }

  const REPORT_FILTER_TABS = [
    { key: 'all', label: 'All Reports' },
    { key: 'lab_report', label: 'Labs' },
    { key: 'imaging_report', label: 'Imaging' },
    { key: 'discharge_summary', label: 'Discharge' },
    { key: 'prescription', label: 'Prescriptions' },
  ]

  return (
    <YStack flex={1} backgroundColor="$background">
      {/* Header */}
      <YStack
        paddingHorizontal="$4"
        paddingTop="$4"
        paddingBottom="$2"
        gap="$2"
      >
        <XStack justifyContent="space-between" alignItems="flex-start">
          <YStack flex={1} gap="$1">
            <H2 color="$color" size="$7">
              Diagnostic Reports & Documents
            </H2>
            <Paragraph color="$color10" size="$3">
              Clinical lab evaluations, pathology summaries, and ingested health documents.
            </Paragraph>
          </YStack>
          <Button
            size="$3"
            theme="blue"
            onPress={() => router.push('/patient/records/import')}
            accessibilityRole="button"
            accessibilityLabel="Add Document or Record"
          >
            + Add Record
          </Button>
        </XStack>

        {/* Filter Pills */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <XStack gap="$2" paddingVertical="$2">
            {REPORT_FILTER_TABS.map((tab) => {
              const active = selectedTypeFilter === tab.key
              return (
                <Button
                  key={tab.key}
                  size="$2.5"
                  theme={active ? 'blue' : undefined}
                  backgroundColor={active ? '$blue9' : '$backgroundHover'}
                  borderRadius="$3"
                  onPress={() => handleSelectFilter(tab.key)}
                  accessibilityRole="button"
                  accessibilityLabel={`Filter by ${tab.label}`}
                  accessibilityState={{ selected: active }}
                >
                  <Text color={active ? 'white' : '$color'} fontSize="$2" fontWeight={active ? '700' : '500'}>
                    {tab.label}
                  </Text>
                </Button>
              )
            })}
          </XStack>
        </ScrollView>
      </YStack>

      <Separator />

      <ScrollView
        contentContainerStyle={{
          paddingHorizontal: 16,
          paddingTop: 16,
          paddingBottom: insets.bottom + 96,
          gap: 12,
        }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true)
              void loadReports(null, false)
            }}
          />
        }
      >
        {error ? (
          <YStack
            backgroundColor="$red4"
            padding="$3"
            borderRadius="$3"
            gap="$2"
          >
            <XStack justifyContent="space-between" alignItems="center">
              <YStack gap="$1" flex={1}>
                <Text color="$red11" fontSize="$3" fontWeight="600">
                  ⚠️ Notice
                </Text>
                <Paragraph color="$red11" size="$2">
                  {error}
                </Paragraph>
              </YStack>
              <Button
                size="$2.5"
                backgroundColor="$red6"
                pressStyle={{ backgroundColor: '$red7' }}
                onPress={() => void loadReports(null, false)}
                accessibilityRole="button"
                accessibilityLabel="Retry loading reports"
              >
                <Text color="$red11" fontSize="$2" fontWeight="700">
                  Retry
                </Text>
              </Button>
            </XStack>
          </YStack>
        ) : null}

        {loading ? (
          <YStack
            alignItems="center"
            justifyContent="center"
            paddingVertical="$10"
            gap="$3"
          >
            <Spinner size="large" color="$blue10" />
            <Paragraph color="$color10">Loading reports…</Paragraph>
          </YStack>
        ) : reports.length === 0 ? (
          <YStack
            alignItems="center"
            justifyContent="center"
            paddingVertical="$10"
            gap="$2"
          >
            <Text fontSize={48}>📄</Text>
            <Paragraph color="$color" size="$4" fontWeight="600">
              {selectedTypeFilter !== 'all'
                ? `No ${
                    REPORT_FILTER_TABS.find((t) => t.key === selectedTypeFilter)?.label ||
                    'matching'
                  } reports on file.`
                : 'No diagnostic reports or documents on file.'}
            </Paragraph>
            <Paragraph color="$color10" size="$3" opacity={0.7} textAlign="center">
              {selectedTypeFilter !== 'all'
                ? 'There are no documents matching this filter in your health record.'
                : 'Reports from hospitals, lab evaluations, and clinical documents will be cataloged here.'}
            </Paragraph>
            {selectedTypeFilter !== 'all' ? (
              <Button
                size="$2.5"
                theme="blue"
                marginTop="$2"
                onPress={() => handleSelectFilter('all')}
                accessibilityRole="button"
                accessibilityLabel="Show all reports"
              >
                Show All Reports
              </Button>
            ) : null}
          </YStack>
        ) : (
          reports.map((item) => (
            <YStack
              key={item.report_id}
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$3.5"
              gap="$2"
              pressStyle={{ opacity: 0.85 }}
              onPress={() => setSelectedReport(item)}
              accessibilityRole="button"
              accessibilityLabel={`View report ${item.report_title}`}
            >
              <XStack justifyContent="space-between" alignItems="center">
                <XStack alignItems="center" gap="$2" flex={1}>
                  <Text fontSize={20}>📄</Text>
                  <YStack flex={1}>
                    <Text color="$color" fontSize="$4" fontWeight="700">
                      {item.report_title}
                    </Text>
                    <Paragraph color="$color10" size="$2">
                      Type: {item.document_type}
                    </Paragraph>
                  </YStack>
                </XStack>
              </XStack>

              <XStack justifyContent="space-between" alignItems="center">
                <Text color="$color10" fontSize="$2">
                  {item.uploaded_at
                    ? `Uploaded ${new Date(item.uploaded_at).toLocaleDateString('en-IN', {
                        dateStyle: 'medium',
                      })}`
                    : 'Upload date not recorded'}
                </Text>
                <Text color="$blue10" fontSize="$2" fontWeight="600">
                  Inspect Metadata →
                </Text>
              </XStack>

              <Paragraph color="$color10" size="$2" opacity={0.7}>
                {item.source_display}
              </Paragraph>
            </YStack>
          ))
        )}

        {nextCursor ? (
          <YStack alignItems="center" paddingVertical="$3">
            <Button
              size="$3"
              theme="blue"
              disabled={loadingOlder}
              onPress={() => void loadReports(nextCursor, true)}
            >
              {loadingOlder ? (
                <XStack gap="$2" alignItems="center">
                  <Spinner size="small" color="white" />
                  <Text color="white">Loading older reports…</Text>
                </XStack>
              ) : (
                'Load older reports'
              )}
            </Button>
          </YStack>
        ) : null}

        {!nextCursor && reports.length > 0 ? (
          <YStack alignItems="center" paddingVertical="$4">
            <Paragraph color="$color10" size="$2" opacity={0.6}>
              ✓ All diagnostic reports loaded
            </Paragraph>
          </YStack>
        ) : null}
      </ScrollView>

      {/* Footer Navigation */}
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
          onPress={() => router.push('/patient/records')}
        >
          ← All Categorized Records
        </Button>
      </YStack>

      {/* Detail Modal */}
      <PatientRecordDetailModal
        open={selectedReport !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedReport(null)
        }}
        category="documents"
        recordId={selectedReport?.report_id || null}
        initialTitle={selectedReport?.report_title}
        initialFields={{
          DocumentType: selectedReport?.document_type,
          UploadedAt: selectedReport?.uploaded_at,
          Source: selectedReport?.source,
        }}
        initialProvenance={{
          source: selectedReport?.source || 'patient_uploaded',
          source_display: selectedReport?.source_display,
          has_source_document: true,
        }}
        initialRecordedAt={selectedReport?.uploaded_at}
      />
    </YStack>
  )
}
