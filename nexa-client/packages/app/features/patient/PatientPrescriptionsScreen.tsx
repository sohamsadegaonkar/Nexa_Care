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
  type PatientPrescriptionItem,
} from '../../utils/apiClient'
import RiskBadge, { type RiskLevel } from './badges/RiskBadge'
import SourceBadge from './badges/SourceBadge'
import PatientRecordDetailModal from './PatientRecordDetailModal'

export default function PatientPrescriptionsScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [prescriptions, setPrescriptions] = useState<PatientPrescriptionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedPrescription, setSelectedPrescription] = useState<PatientPrescriptionItem | null>(null)

  const loadPrescriptions = useCallback(async (cursor?: string | null, append = false) => {
    if (append) setLoadingOlder(true)
    else if (!cursor) setLoading(true)
    setError(null)

    try {
      const res = await NexaApiClient.getMyPrescriptions({
        cursor,
        limit: 20,
      })
      if (append) {
        setPrescriptions((prev) => [...prev, ...res.prescriptions])
      } else {
        setPrescriptions(res.prescriptions)
      }
      setNextCursor(res.next_cursor)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Failed to load prescriptions and medications'
      )
    } finally {
      setLoading(false)
      setRefreshing(false)
      setLoadingOlder(false)
    }
  }, [])

  useEffect(() => {
    void loadPrescriptions()
  }, [loadPrescriptions])

  return (
    <YStack flex={1} backgroundColor="$background">
      {/* Header */}
      <YStack
        paddingHorizontal="$4"
        paddingTop="$4"
        paddingBottom="$2"
        gap="$2"
      >
        <H2 color="$color" size="$7">
          Prescriptions & Medications
        </H2>
        <Paragraph color="$color10" size="$3">
          Active and historical pharmaceutical treatments with clinical provenance.
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
              setRefreshing(true)
              void loadPrescriptions(null, false)
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
            <Paragraph color="$color10">Loading medications…</Paragraph>
          </YStack>
        ) : prescriptions.length === 0 ? (
          <YStack
            alignItems="center"
            justifyContent="center"
            paddingVertical="$10"
            gap="$2"
          >
            <Text fontSize={48}>💊</Text>
            <Paragraph color="$color10" size="$4">
              No active or historical medications on file.
            </Paragraph>
            <Paragraph color="$color10" size="$3" opacity={0.6} textAlign="center">
              Prescriptions entered by your treating doctor or extracted from uploaded documents will appear here.
            </Paragraph>
          </YStack>
        ) : (
          prescriptions.map((item) => (
            <YStack
              key={item.prescription_id}
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$3.5"
              gap="$2.5"
              pressStyle={{ opacity: 0.85 }}
              onPress={() => setSelectedPrescription(item)}
              accessibilityRole="button"
              accessibilityLabel={`View details for ${item.medication_name}`}
            >
              <XStack justifyContent="space-between" alignItems="flex-start">
                <YStack flex={1} gap="$1">
                  <XStack alignItems="center" gap="$2">
                    <Text fontSize={18}>💊</Text>
                    <Text color="$color" fontSize="$4" fontWeight="700">
                      {item.medication_name}
                    </Text>
                  </XStack>
                  <Text color="$color11" fontSize="$3" fontWeight="600">
                    {item.strength} • {item.frequency}
                  </Text>
                </YStack>
                <RiskBadge level={(item.risk_level as RiskLevel) || 'LOW_RISK'} />
              </XStack>

              <XStack
                justifyContent="space-between"
                alignItems="center"
                flexWrap="wrap"
                gap="$2"
              >
                <SourceBadge
                  source={item.source === 'manual' ? 'manual' : 'ai_extracted'}
                  confidence={
                    item.confidence != null
                      ? Math.round(item.confidence * 100)
                      : undefined
                  }
                />
                <Text color="$color10" fontSize="$2">
                  {item.prescribed_at
                    ? `Prescribed ${new Date(item.prescribed_at).toLocaleDateString('en-IN', {
                        dateStyle: 'medium',
                      })}`
                    : 'Date not recorded'}
                </Text>
              </XStack>

              <XStack justifyContent="space-between" alignItems="center">
                <Paragraph color="$color10" size="$2" opacity={0.7}>
                  {item.source_display}
                </Paragraph>
                <Text color="$blue10" fontSize="$2" fontWeight="600">
                  Inspect Details →
                </Text>
              </XStack>
            </YStack>
          ))
        )}

        {nextCursor ? (
          <YStack alignItems="center" paddingVertical="$3">
            <Button
              size="$3"
              theme="blue"
              disabled={loadingOlder}
              onPress={() => void loadPrescriptions(nextCursor, true)}
            >
              {loadingOlder ? (
                <XStack gap="$2" alignItems="center">
                  <Spinner size="small" color="white" />
                  <Text color="white">Loading older prescriptions…</Text>
                </XStack>
              ) : (
                'Load older prescriptions'
              )}
            </Button>
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
        open={selectedPrescription !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedPrescription(null)
        }}
        category="medications"
        recordId={selectedPrescription?.prescription_id || null}
        initialTitle={`Medication: ${selectedPrescription?.medication_name || ''}`}
        initialFields={{
          Medication: selectedPrescription?.medication_name,
          Strength: selectedPrescription?.strength,
          Frequency: selectedPrescription?.frequency,
          PrescribedDate: selectedPrescription?.prescribed_at,
        }}
        initialProvenance={{
          source: selectedPrescription?.source || 'manual',
          source_display: selectedPrescription?.source_display,
          confidence: selectedPrescription?.confidence,
          risk_level: selectedPrescription?.risk_level,
          has_source_document: selectedPrescription?.has_source_document,
        }}
        initialRecordedAt={selectedPrescription?.prescribed_at}
      />
    </YStack>
  )
}
