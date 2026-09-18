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
import React, { useCallback, useEffect, useState } from 'react'
import { RefreshControl, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import {
  NexaApiClient,
  type PatientRecordCategoryItem,
} from '../../utils/apiClient'
import PatientRecordDetailModal from './PatientRecordDetailModal'

const CATEGORY_DEFINITIONS: Record<
  string,
  { label: string; icon: string; description: string }
> = {
  allergies: {
    label: 'Allergies',
    icon: '⚠️',
    description: 'Immunological sensitivities and allergic reactions.',
  },
  medications: {
    label: 'Medications',
    icon: '💊',
    description: 'Active and historical pharmaceutical prescriptions.',
  },
  vitals: {
    label: 'Vitals',
    icon: '❤️',
    description: 'Blood pressure, heart rate, oxygen levels, and temperature.',
  },
  labs: {
    label: 'Laboratory',
    icon: '🔬',
    description: 'Blood work, pathology, and diagnostic lab evaluations.',
  },
  documents: {
    label: 'Reports & Documents',
    icon: '📄',
    description: 'Clinical reports, discharge summaries, and uploaded records.',
  },
}

export default function PatientRecordsScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [categories, setCategories] = useState<PatientRecordCategoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Active category drilldown state
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [categoryRecords, setCategoryRecords] = useState<Array<Record<string, any>>>([])
  const [categoryLoading, setCategoryLoading] = useState(false)
  const [categoryNextCursor, setCategoryNextCursor] = useState<string | null>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)

  // Detail modal state
  const [selectedRecord, setSelectedRecord] = useState<Record<string, any> | null>(null)

  const loadCategories = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)

    try {
      const res = await NexaApiClient.getMyRecordCategories()
      setCategories(res.categories)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to load medical records'
      )
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void loadCategories()
  }, [loadCategories])

  const loadCategoryRecords = useCallback(
    async (cat: string, cursor?: string | null, append = false) => {
      if (append) setLoadingOlder(true)
      else setCategoryLoading(true)

      try {
        const res = await NexaApiClient.getMyRecordsByCategory(cat, {
          cursor,
          limit: 20,
        })
        if (append) {
          setCategoryRecords((prev) => [...prev, ...res.records])
        } else {
          setCategoryRecords(res.records)
        }
        setCategoryNextCursor(res.next_cursor)
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : `Failed to load ${cat} records`
        )
      } finally {
        setCategoryLoading(false)
        setLoadingOlder(false)
      }
    },
    []
  )

  const handleSelectCategory = (catKey: string) => {
    setSelectedCategory(catKey)
    void loadCategoryRecords(catKey)
  }

  const handleBackToOverview = () => {
    setSelectedCategory(null)
    setCategoryRecords([])
    setCategoryNextCursor(null)
  }

  return (
    <YStack flex={1} backgroundColor="$background">
      {/* Header */}
      <YStack
        paddingHorizontal="$4"
        paddingTop="$4"
        paddingBottom="$2"
        gap="$2"
      >
        <XStack alignItems="center" justifyContent="space-between">
          <H2 color="$color" size="$7">
            Categorized Records
          </H2>
          {selectedCategory ? (
            <Button
              size="$2"
              chromeless
              onPress={handleBackToOverview}
            >
              ← All Categories
            </Button>
          ) : null}
        </XStack>
        <Paragraph color="$color10" size="$3">
          {selectedCategory
            ? `Viewing all ${CATEGORY_DEFINITIONS[selectedCategory]?.label || selectedCategory} on file.`
            : 'Structured medical records grouped by clinical observation type.'}
        </Paragraph>
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
              if (selectedCategory) {
                void loadCategoryRecords(selectedCategory)
              } else {
                void loadCategories(true)
              }
            }}
          />
        }
      >
        {error ? (
          <YStack
            backgroundColor="$red4"
            padding="$3"
            borderRadius="$3"
            gap="$1"
          >
            <Text color="$red11" fontSize="$3" fontWeight="600">
              ⚠️ Notice
            </Text>
            <Paragraph color="$red11" size="$2">
              {error}
            </Paragraph>
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
            <Paragraph color="$color10">Loading medical records…</Paragraph>
          </YStack>
        ) : selectedCategory ? (
          /* Category Drilldown List */
          <YStack gap="$3">
            {categoryLoading ? (
              <YStack
                alignItems="center"
                justifyContent="center"
                paddingVertical="$8"
                gap="$2"
              >
                <Spinner size="large" color="$blue10" />
                <Paragraph color="$color10">Loading records…</Paragraph>
              </YStack>
            ) : categoryRecords.length === 0 ? (
              <YStack
                alignItems="center"
                justifyContent="center"
                paddingVertical="$8"
                gap="$2"
              >
                <Text fontSize={40}>📋</Text>
                <Paragraph color="$color10" size="$4">
                  No {CATEGORY_DEFINITIONS[selectedCategory]?.label} recorded on file.
                </Paragraph>
              </YStack>
            ) : (
              categoryRecords.map((item) => (
                <YStack
                  key={item.record_id}
                  backgroundColor="$backgroundHover"
                  borderRadius="$4"
                  padding="$3.5"
                  gap="$2"
                  pressStyle={{ opacity: 0.85 }}
                  onPress={() => setSelectedRecord(item)}
                >
                  <XStack justifyContent="space-between" alignItems="center">
                    <Text color="$color" fontSize="$4" fontWeight="700">
                      {item.type || item.name || item.test_name || item.allergen || item.document_type || 'Record'}
                    </Text>
                    {item.value ? (
                      <Text color="$color" fontSize="$4" fontWeight="800">
                        {item.value} {item.unit || ''}
                      </Text>
                    ) : item.strength ? (
                      <Text color="$color" fontSize="$3" fontWeight="600">
                        {item.strength}
                      </Text>
                    ) : null}
                  </XStack>

                  <XStack justifyContent="space-between" alignItems="center">
                    <Text color="$color10" fontSize="$2">
                      {item.recorded_at || item.prescribed_at || item.uploaded_at
                        ? new Date(
                            item.recorded_at || item.prescribed_at || item.uploaded_at
                          ).toLocaleDateString('en-IN', {
                            dateStyle: 'medium',
                          })
                        : 'Date not recorded'}
                    </Text>
                    <Text color="$blue10" fontSize="$2" fontWeight="600">
                      View Details →
                    </Text>
                  </XStack>
                </YStack>
              ))
            )}

            {categoryNextCursor ? (
              <YStack alignItems="center" paddingVertical="$3">
                <Button
                  size="$3"
                  theme="blue"
                  disabled={loadingOlder}
                  onPress={() =>
                    void loadCategoryRecords(selectedCategory, categoryNextCursor, true)
                  }
                >
                  {loadingOlder ? (
                    <XStack gap="$2" alignItems="center">
                      <Spinner size="small" color="white" />
                      <Text color="white">Loading older records…</Text>
                    </XStack>
                  ) : (
                    'Load older records'
                  )}
                </Button>
              </YStack>
            ) : null}
          </YStack>
        ) : (
          /* Category Overview Grid */
          <YStack gap="$3">
            {categories.map((cat) => {
              const def = CATEGORY_DEFINITIONS[cat.category] || {
                label: cat.label,
                icon: cat.icon || '📁',
                description: '',
              }
              return (
                <YStack
                  key={cat.category}
                  backgroundColor="$backgroundHover"
                  borderRadius="$4"
                  padding="$4"
                  gap="$2.5"
                  pressStyle={{ opacity: 0.85 }}
                  onPress={() => handleSelectCategory(cat.category)}
                  accessibilityRole="button"
                  accessibilityLabel={`Browse ${def.label}`}
                >
                  <XStack justifyContent="space-between" alignItems="center">
                    <XStack alignItems="center" gap="$3">
                      <Text fontSize={24}>{def.icon}</Text>
                      <YStack>
                        <Text color="$color" fontSize="$5" fontWeight="800">
                          {def.label}
                        </Text>
                        <Paragraph color="$color10" size="$2">
                          {def.description}
                        </Paragraph>
                      </YStack>
                    </XStack>
                    <YStack
                      backgroundColor="$blue4"
                      paddingHorizontal="$3"
                      paddingVertical="$1.5"
                      borderRadius="$3"
                    >
                      <Text color="$blue11" fontSize="$3" fontWeight="800">
                        {cat.count}
                      </Text>
                    </YStack>
                  </XStack>

                  {cat.preview ? (
                    <Paragraph color="$color11" size="$2" opacity={0.8}>
                      Latest: {cat.preview}
                    </Paragraph>
                  ) : (
                    <Paragraph color="$color10" size="$2" opacity={0.5}>
                      No records on file
                    </Paragraph>
                  )}
                </YStack>
              )
            })}
          </YStack>
        )}
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
          onPress={() => router.push('/patient/timeline')}
        >
          ← View Complete Health Timeline
        </Button>
      </YStack>

      {/* Detail Modal */}
      <PatientRecordDetailModal
        open={selectedRecord !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedRecord(null)
        }}
        category={selectedCategory || 'vitals'}
        recordId={selectedRecord?.record_id || null}
        initialTitle={
          selectedRecord?.type ||
          selectedRecord?.name ||
          selectedRecord?.test_name ||
          selectedRecord?.allergen ||
          selectedRecord?.document_type ||
          'Record Details'
        }
        initialFields={selectedRecord || {}}
        initialProvenance={{
          source: selectedRecord?.source || 'manual',
          confidence: selectedRecord?.confidence,
          risk_level: selectedRecord?.risk_level,
          has_source_document: selectedRecord?.has_source_document,
        }}
        initialRecordedAt={
          selectedRecord?.recorded_at ||
          selectedRecord?.prescribed_at ||
          selectedRecord?.uploaded_at
        }
      />
    </YStack>
  )
}
