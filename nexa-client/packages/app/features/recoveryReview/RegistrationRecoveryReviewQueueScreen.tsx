'use client'

import {
  ActionButton,
  InlineNotice,
  Paragraph,
  ScreenHeader,
  StatusBadge,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { useRouter } from 'solito/navigation'
import { useCallback, useEffect, useState } from 'react'
import { ScrollView } from 'react-native'
import {
  RegistrationRecoveryReviewStatus,
  ReviewerCaseResponse,
  listReviewerCases,
} from '../../services/patientRegistrationRecoveryReview'

interface RegistrationRecoveryReviewQueueScreenProps {
  onSelectCase?: (caseReference: string) => void
}

const FILTER_TABS: Array<{ label: string; value: RegistrationRecoveryReviewStatus | 'ALL' }> = [
  { label: 'All', value: 'ALL' },
  { label: 'Pending', value: 'PENDING' },
  { label: 'In review', value: 'IN_REVIEW' },
  { label: 'Resolved', value: 'RESOLVED' },
  { label: 'Rejected', value: 'REJECTED' },
  { label: 'Escalated', value: 'SECURITY_ESCALATED' },
]

export default function RegistrationRecoveryReviewQueueScreen({
  onSelectCase,
}: RegistrationRecoveryReviewQueueScreenProps) {
  const router = useRouter()
  const [cases, setCases] = useState<ReviewerCaseResponse[]>([])
  const [statusFilter, setStatusFilter] = useState<RegistrationRecoveryReviewStatus | 'ALL'>('ALL')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchCases = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await listReviewerCases({
        status: statusFilter === 'ALL' ? undefined : statusFilter,
        limit: 50,
      })
      setCases(response.cases || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load reviewer queue.')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    void fetchCases()
  }, [fetchCases])

  const handleOpenCase = (caseReference: string) => {
    if (onSelectCase) {
      onSelectCase(caseReference)
    } else {
      router.push(`/doctor/recovery-review/${encodeURIComponent(caseReference)}`)
    }
  }

  const getStatusTone = (status: RegistrationRecoveryReviewStatus) => {
    switch (status) {
      case 'RESOLVED':
        return 'success'
      case 'REJECTED':
      case 'SECURITY_ESCALATED':
        return 'danger'
      default:
        return 'warning'
    }
  }

  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ flexGrow: 1, padding: 16 }}>
      <YStack gap="$4" maxWidth={1000} width="100%" alignSelf="center">
        <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$3">
          <YStack gap="$1" flex={1}>
            <StatusBadge tone="neutral">Provider Administration</StatusBadge>
            <ScreenHeader
              title="Registration Recovery Review Queue"
              description="Review and disposition flagged patient registration-recovery cases requiring manual clinician adjudication."
            />
          </YStack>
          <ActionButton
            disabled={loading}
            onPress={() => void fetchCases()}
            size="$3"
          >
            {loading ? 'Refreshing...' : 'Refresh queue'}
          </ActionButton>
        </XStack>

        {/* Filter Pills */}
        <XStack gap="$2" flexWrap="wrap">
          {FILTER_TABS.map((tab) => {
            const isSelected = statusFilter === tab.value
            return (
              <ActionButton
                key={tab.value}
                size="$2"
                intent={isSelected ? 'primary' : undefined}
                onPress={() => setStatusFilter(tab.value)}
              >
                {tab.label}
              </ActionButton>
            )
          })}
        </XStack>

        {error !== null ? (
          <InlineNotice title={error} tone="danger" />
        ) : null}

        {loading ? (
          <YStack padding="$6" alignItems="center" justifyContent="center">
            <Paragraph color="$nexaSecondary">Loading reviewer cases...</Paragraph>
          </YStack>
        ) : cases.length === 0 ? (
          <YStack
            padding="$6"
            borderRadius="$4"
            backgroundColor="$nexaSurface"
            borderColor="$nexaBorder"
            borderWidth={1}
            alignItems="center"
            justifyContent="center"
            gap="$2"
          >
            <Paragraph fontWeight="600" color="$nexaText">
              No review cases found
            </Paragraph>
            <Paragraph color="$nexaSecondary" fontSize={13}>
              No registration recovery cases currently match the selected filter.
            </Paragraph>
          </YStack>
        ) : (
          <YStack gap="$3">
            {cases.map((item) => (
              <YStack
                key={item.case_reference}
                padding="$4"
                borderRadius="$4"
                backgroundColor="$nexaSurface"
                borderColor="$nexaBorder"
                borderWidth={1}
                gap="$3"
              >
                <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
                  <XStack gap="$2" alignItems="center">
                    <Paragraph fontWeight="700" fontSize={16} color="$nexaText">
                      {item.case_reference}
                    </Paragraph>
                    <StatusBadge tone={getStatusTone(item.status)}>
                      {item.status}
                    </StatusBadge>
                  </XStack>
                  <XStack gap="$2" alignItems="center">
                    {item.assigned_to_current_reviewer ? (
                      <StatusBadge tone="success">Assigned to you</StatusBadge>
                    ) : item.status === 'PENDING' ? (
                      <StatusBadge tone="neutral">Unclaimed</StatusBadge>
                    ) : (
                      <StatusBadge tone="neutral">Other reviewer</StatusBadge>
                    )}
                    <Paragraph color="$nexaSecondary" fontSize={12}>
                      Version {item.version}
                    </Paragraph>
                  </XStack>
                </XStack>

                <XStack gap="$2" flexWrap="wrap">
                  {item.reason_codes.map((reason) => (
                    <Text
                      key={reason}
                      fontSize={11}
                      paddingHorizontal="$2"
                      paddingVertical="$1"
                      borderRadius="$2"
                      backgroundColor="$nexaCanvas"
                      color="$nexaSecondary"
                    >
                      {reason}
                    </Text>
                  ))}
                </XStack>

                <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Created: {new Date(item.created_at).toLocaleString()}
                  </Paragraph>
                  <ActionButton
                    size="$3"
                    intent="primary"
                    onPress={() => handleOpenCase(item.case_reference)}
                  >
                    View case
                  </ActionButton>
                </XStack>
              </YStack>
            ))}
          </YStack>
        )}
      </YStack>
    </ScrollView>
  )
}
