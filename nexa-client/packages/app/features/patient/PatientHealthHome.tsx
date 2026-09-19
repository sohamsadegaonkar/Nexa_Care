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
  type PatientHealthSummaryResponse,
} from '../../utils/apiClient'
import RiskBadge, { type RiskLevel } from './badges/RiskBadge'
import SourceBadge from './badges/SourceBadge'

export default function PatientHealthHome() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [summary, setSummary] = useState<PatientHealthSummaryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isMountedRef = React.useRef(true)

  React.useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const loadSummary = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)

    try {
      const res = await NexaApiClient.getMyHealthSummary()
      if (isMountedRef.current) {
        setSummary(res)
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(
          err instanceof Error ? err.message : 'Failed to load health summary'
        )
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    void loadSummary()
  }, [loadSummary])

  return (
    <YStack flex={1} backgroundColor="$background">
      {/* Top Banner */}
      <YStack
        paddingHorizontal="$4"
        paddingTop="$4"
        paddingBottom="$2"
        gap="$2"
      >
        <XStack justifyContent="space-between" alignItems="center">
          <YStack gap="$1">
            <H2 color="$color" size="$7">
              My Health Home
            </H2>
            <Paragraph color="$color10" size="$3">
              Longitudinal healthcare summary and medical records.
            </Paragraph>
          </YStack>
          <Button
            size="$2"
            chromeless
            onPress={() => router.push('/patient/access-history')}
            accessibilityRole="button"
            accessibilityLabel="View Access History"
          >
            Access History →
          </Button>
        </XStack>
      </YStack>

      <Separator />

      <ScrollView
        contentContainerStyle={{
          paddingHorizontal: 16,
          paddingTop: 16,
          paddingBottom: insets.bottom + 96,
          gap: 16,
        }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => void loadSummary(true)}
          />
        }
      >
        {error ? (
          <YStack
            backgroundColor="$red4"
            padding="$3"
            borderRadius="$3"
            gap="$2"
            accessibilityRole="alert"
          >
            <XStack justifyContent="space-between" alignItems="center">
              <Text color="$red11" fontSize="$3" fontWeight="600">
                ⚠️ Notice
              </Text>
              <Button
                size="$2"
                theme="red"
                onPress={() => void loadSummary(false)}
                accessibilityRole="button"
                accessibilityLabel="Retry loading health summary"
              >
                Retry
              </Button>
            </XStack>
            <Paragraph color="$red11" size="$2">
              {error}
            </Paragraph>
          </YStack>
        ) : null}

        {/* Quick Navigation Action Tiles */}
        <XStack gap="$3" flexWrap="wrap">
          <YStack
            flex={1}
            minWidth={140}
            backgroundColor="$backgroundHover"
            padding="$3.5"
            borderRadius="$4"
            gap="$1.5"
            pressStyle={{ opacity: 0.8 }}
            onPress={() => router.push('/patient/timeline')}
            accessibilityRole="button"
            accessibilityLabel="Open Health Timeline"
          >
            <Text fontSize={24}>📅</Text>
            <Text color="$color" fontSize="$4" fontWeight="700">
              Timeline
            </Text>
            <Paragraph color="$color10" size="$1">
              Chronological history
            </Paragraph>
          </YStack>

          <YStack
            flex={1}
            minWidth={140}
            backgroundColor="$backgroundHover"
            padding="$3.5"
            borderRadius="$4"
            gap="$1.5"
            pressStyle={{ opacity: 0.8 }}
            onPress={() => router.push('/patient/records')}
            accessibilityRole="button"
            accessibilityLabel="Open Categorized Records"
          >
            <Text fontSize={24}>📁</Text>
            <Text color="$color" fontSize="$4" fontWeight="700">
              Records
            </Text>
            <Paragraph color="$color10" size="$1">
              Browse by category
            </Paragraph>
          </YStack>

          <YStack
            flex={1}
            minWidth={140}
            backgroundColor="$backgroundHover"
            padding="$3.5"
            borderRadius="$4"
            gap="$1.5"
            pressStyle={{ opacity: 0.8 }}
            onPress={() => router.push('/patient/prescriptions')}
            accessibilityRole="button"
            accessibilityLabel="Open Prescriptions"
          >
            <Text fontSize={24}>💊</Text>
            <Text color="$color" fontSize="$4" fontWeight="700">
              Prescriptions
            </Text>
            <Paragraph color="$color10" size="$1">
              Active medications
            </Paragraph>
          </YStack>


          <YStack
            flex={1}
            minWidth={140}
            backgroundColor="$backgroundHover"
            padding="$3.5"
            borderRadius="$4"
            gap="$1.5"
            pressStyle={{ opacity: 0.8 }}
            onPress={() => router.push('/patient/treatment-request')}
            accessibilityRole="button"
            accessibilityLabel="Review Treatment Session request"
          >
            <Text fontSize={24}>🩺</Text>
            <Text color="$color" fontSize="$4" fontWeight="700">
              Treatment Approval
            </Text>
            <Paragraph color="$color10" size="$1">
              Review provider operations
            </Paragraph>
          </YStack>

          <YStack
            flex={1}
            minWidth={140}
            backgroundColor="$backgroundHover"
            padding="$3.5"
            borderRadius="$4"
            gap="$1.5"
            pressStyle={{ opacity: 0.8 }}
            onPress={() => router.push('/patient/reports')}
            accessibilityRole="button"
            accessibilityLabel="Open Diagnostic Reports"
          >
            <Text fontSize={24}>📄</Text>
            <Text color="$color" fontSize="$4" fontWeight="700">
              Reports
            </Text>
            <Paragraph color="$color10" size="$1">
              Diagnostic documents
            </Paragraph>
          </YStack>
        </XStack>

        {loading ? (
          <YStack
            alignItems="center"
            justifyContent="center"
            paddingVertical="$10"
            gap="$3"
          >
            <Spinner size="large" color="$blue10" />
            <Paragraph color="$color10">Loading health summary…</Paragraph>
          </YStack>
        ) : (
          <YStack gap="$4">
            {/* Active Medications Summary Card */}
            <YStack
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$4"
              gap="$3"
            >
              <XStack justifyContent="space-between" alignItems="center">
                <XStack alignItems="center" gap="$2">
                  <Text fontSize={20}>💊</Text>
                  <Text color="$color" fontSize="$5" fontWeight="800">
                    Active Medications
                  </Text>
                </XStack>
                <Button
                  size="$2"
                  chromeless
                  onPress={() => router.push('/patient/prescriptions')}
                  accessibilityRole="button"
                  accessibilityLabel="View all active medications"
                >
                  View All →
                </Button>
              </XStack>

              {summary && summary.active_medications.length > 0 ? (
                <YStack gap="$2">
                  {summary.active_medications.map((m, idx) => (
                    <XStack
                      key={idx}
                      justifyContent="space-between"
                      alignItems="center"
                      paddingVertical="$1"
                    >
                      <YStack>
                        <Text color="$color" fontSize="$3" fontWeight="700">
                          {m.medication_name}
                        </Text>
                        <Paragraph color="$color10" size="$2">
                          {m.dosage} • {m.frequency}
                        </Paragraph>
                      </YStack>
                      <SourceBadge
                        source={m.source}
                      />
                    </XStack>
                  ))}
                </YStack>
              ) : (
                <Paragraph color="$color10" size="$2" opacity={0.7}>
                  No active medications recorded on file.
                </Paragraph>
              )}
            </YStack>

            {/* Allergies Summary Card */}
            <YStack
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$4"
              gap="$3"
            >
              <XStack justifyContent="space-between" alignItems="center">
                <XStack alignItems="center" gap="$2">
                  <Text fontSize={20}>⚠️</Text>
                  <Text color="$color" fontSize="$5" fontWeight="800">
                    Allergies & Sensitivities
                  </Text>
                </XStack>
                <Button
                  size="$2"
                  chromeless
                  onPress={() => router.push('/patient/records')}
                  accessibilityRole="button"
                  accessibilityLabel="View allergy details in medical records"
                >
                  Details →
                </Button>
              </XStack>

              {summary && summary.allergy_highlights.length > 0 ? (
                <YStack gap="$2">
                  {summary.allergy_highlights.map((a, idx) => (
                    <XStack
                      key={idx}
                      justifyContent="space-between"
                      alignItems="center"
                      paddingVertical="$1"
                    >
                      <YStack>
                        <Text color="$color" fontSize="$3" fontWeight="700">
                          {a.allergen}
                        </Text>
                        <Paragraph color="$color10" size="$2">
                          Severity: {a.severity}
                        </Paragraph>
                      </YStack>
                      <RiskBadge level={(a.risk_level as RiskLevel) || 'HIGH_RISK'} />
                    </XStack>
                  ))}
                </YStack>
              ) : (
                <Paragraph color="$color10" size="$2" opacity={0.7}>
                  No recorded allergies on file.
                </Paragraph>
              )}
            </YStack>

            {/* Latest Vitals Summary Card */}
            <YStack
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$4"
              gap="$3"
            >
              <XStack justifyContent="space-between" alignItems="center">
                <XStack alignItems="center" gap="$2">
                  <Text fontSize={20}>❤️</Text>
                  <Text color="$color" fontSize="$5" fontWeight="800">
                    Recent Vitals
                  </Text>
                </XStack>
                <Button
                  size="$2"
                  chromeless
                  onPress={() => router.push('/patient/records')}
                  accessibilityRole="button"
                  accessibilityLabel="View vitals history in medical records"
                >
                  History →
                </Button>
              </XStack>

              {summary && summary.latest_vitals.length > 0 ? (
                <XStack gap="$3" flexWrap="wrap">
                  {summary.latest_vitals.map((v, idx) => (
                    <YStack
                      key={idx}
                      flex={1}
                      minWidth={110}
                      backgroundColor="$background"
                      padding="$2.5"
                      borderRadius="$3"
                    >
                      <Text color="$color10" fontSize="$1" fontWeight="600" textTransform="uppercase">
                        {v.type}
                      </Text>
                      <Text color="$color" fontSize="$4" fontWeight="800">
                        {v.value} {v.unit}
                      </Text>
                    </YStack>
                  ))}
                </XStack>
              ) : (
                <Paragraph color="$color10" size="$2" opacity={0.7}>
                  No vitals recorded on file.
                </Paragraph>
              )}
            </YStack>

            {/* Recent Diagnostic Labs / Reports */}
            <YStack
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$4"
              gap="$3"
            >
              <XStack justifyContent="space-between" alignItems="center">
                <XStack alignItems="center" gap="$2">
                  <Text fontSize={20}>🔬</Text>
                  <Text color="$color" fontSize="$5" fontWeight="800">
                    Recent Lab Evaluations
                  </Text>
                </XStack>
                <Button
                  size="$2"
                  chromeless
                  onPress={() => router.push('/patient/reports')}
                  accessibilityRole="button"
                  accessibilityLabel="View all laboratory evaluations and reports"
                >
                  All Reports →
                </Button>
              </XStack>

              {summary && summary.recent_labs.length > 0 ? (
                <YStack gap="$2">
                  {summary.recent_labs.map((l, idx) => (
                    <XStack
                      key={idx}
                      justifyContent="space-between"
                      alignItems="center"
                      paddingVertical="$1"
                    >
                      <YStack>
                        <Text color="$color" fontSize="$3" fontWeight="700">
                          {l.test_name}
                        </Text>
                        <Paragraph color="$color10" size="$2">
                          Result: {l.value} {l.unit}
                        </Paragraph>
                      </YStack>
                      {l.is_abnormal ? (
                        <YStack
                          backgroundColor="$red5"
                          borderRadius="$2"
                          paddingHorizontal="$2"
                          paddingVertical="$1"
                        >
                          <Text color="$red10" fontSize="$1" fontWeight="700">
                            ABNORMAL
                          </Text>
                        </YStack>
                      ) : (
                        <Text color="$color10" fontSize="$2">
                          Final
                        </Text>
                      )}
                    </XStack>
                  ))}
                </YStack>
              ) : (
                <Paragraph color="$color10" size="$2" opacity={0.7}>
                  No laboratory evaluations recorded.
                </Paragraph>
              )}
            </YStack>
          </YStack>
        )}
      </ScrollView>
    </YStack>
  )
}
