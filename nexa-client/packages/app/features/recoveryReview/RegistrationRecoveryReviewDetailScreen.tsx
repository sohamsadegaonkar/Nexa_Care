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
import { useCallback, useEffect, useId, useState } from 'react'
import { ScrollView } from 'react-native'
import {
  RegistrationRecoveryReviewOutcome,
  RegistrationRecoveryReviewReason,
  RegistrationRecoveryReviewStatus,
  ReviewerCaseResponse,
  claimReviewerCase,
  getReviewerCase,
  recoverReviewerSession,
  resolveReviewerCase,
} from '../../services/patientRegistrationRecoveryReview'

interface RegistrationRecoveryReviewDetailScreenProps {
  caseReference: string
  onBack?: () => void
}

const OUTCOMES: Array<{
  value: RegistrationRecoveryReviewOutcome
  label: string
  description: string
  isRepair: boolean
}> = [
  {
    value: 'RESTORE_MISSING_RECORD_ANCHOR',
    label: 'Restore missing record anchor',
    description: 'Rebuilds the clinical record root pointer for an orphaned registration graph.',
    isRepair: true,
  },
  {
    value: 'REBIND_MERGED_IDENTITY',
    label: 'Rebind merged identity',
    description: 'Re-associates the verified phone identity with an updated merged canonical patient.',
    isRepair: true,
  },
  {
    value: 'NO_REPAIR',
    label: 'No repair (Reject recovery)',
    description: 'Denies automated recovery. Patient must contact hospital administration.',
    isRepair: false,
  },
  {
    value: 'SECURITY_ESCALATION_REQUIRED',
    label: 'Security escalation required',
    description: 'Flags account for elevated security audit. Prohibits self-service account repair.',
    isRepair: false,
  },
]

const ALL_REASONS: RegistrationRecoveryReviewReason[] = [
  'MISSING_RECORD_ANCHOR',
  'MERGED_IDENTITY_REBIND_REQUIRED',
  'IDENTITY_REVOKED',
  'PATIENT_DELETED_WITHOUT_MERGE',
  'ERASURE_STATE_PRESENT',
  'MULTIPLE_IDENTITIES',
  'MERGE_AMBIGUOUS',
  'GRAPH_STATE_CHANGED',
  'SECURITY_CONCERN',
]

function generateClientDurableIdempotencyKey(caseReference: string): string {
  const randomPart = Math.random().toString(36).slice(2, 12)
  const timePart = Date.now().toString(36)
  return `rrr-resolve-${caseReference.replace(/[^a-zA-Z0-9]/g, '')}-${timePart}-${randomPart}`
}

export default function RegistrationRecoveryReviewDetailScreen({
  caseReference,
  onBack,
}: RegistrationRecoveryReviewDetailScreenProps) {
  const router = useRouter()
  const [caseData, setCaseData] = useState<ReviewerCaseResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionBusy, setActionBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflictMessage, setConflictMessage] = useState<string | null>(null)
  const [sessionMismatch, setSessionMismatch] = useState(false)

  // Resolution form state
  const [selectedOutcome, setSelectedOutcome] =
    useState<RegistrationRecoveryReviewOutcome>('NO_REPAIR')
  const [selectedReasons, setSelectedReasons] = useState<
    RegistrationRecoveryReviewReason[]
  >([])
  const [durableIdempotencyKey, setDurableIdempotencyKey] = useState<string>(() =>
    generateClientDurableIdempotencyKey(caseReference)
  )

  const loadCase = useCallback(
    async (preserveConflict = false) => {
      setLoading(true)
      setError(null)
      if (!preserveConflict) setConflictMessage(null)
      setSessionMismatch(false)
      try {
        const data = await getReviewerCase(caseReference)
        setCaseData(data)
        // Pre-select existing reasons
        if (data.reason_codes?.length) {
          setSelectedReasons(data.reason_codes.slice(0, 4))
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to load case details.')
      } finally {
        setLoading(false)
      }
    },
    [caseReference]
  )

  useEffect(() => {
    void loadCase()
  }, [loadCase])

  const handleBack = () => {
    if (onBack) {
      onBack()
    } else {
      router.replace('/doctor/recovery-review')
    }
  }

  const handleClaim = async () => {
    if (!caseData || actionBusy) return
    setActionBusy(true)
    setError(null)
    setConflictMessage(null)
    try {
      const updated = await claimReviewerCase(caseReference, caseData.version)
      setCaseData(updated)
    } catch (err: any) {
      if (
        err?.code === 'REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT' ||
        err?.code === 'REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT'
      ) {
        setConflictMessage(
          'Case version conflict: this case was claimed or modified by another reviewer. Refreshed latest state.'
        )
        await loadCase(true)
      } else {
        setError(err instanceof Error ? err.message : 'Failed to claim case.')
      }
    } finally {
      setActionBusy(false)
    }
  }

  const handleRecoverSession = async () => {
    if (!caseData || actionBusy) return
    setActionBusy(true)
    setError(null)
    setSessionMismatch(false)
    try {
      const updated = await recoverReviewerSession(caseReference, caseData.version)
      setCaseData(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to recover review session.')
    } finally {
      setActionBusy(false)
    }
  }

  const toggleReason = (reason: RegistrationRecoveryReviewReason) => {
    setSelectedReasons((current) => {
      if (current.includes(reason)) {
        if (current.length === 1) return current // At least 1 required
        return current.filter((r) => r !== reason)
      }
      if (current.length >= 4) return current // Maximum 4 allowed
      return [...current, reason]
    })
  }

  const handleResolve = async () => {
    if (!caseData || actionBusy || selectedReasons.length === 0) return
    setActionBusy(true)
    setError(null)
    setConflictMessage(null)
    setSessionMismatch(false)

    try {
      const result = await resolveReviewerCase(caseReference, {
        expectedVersion: caseData.version,
        idempotencyKey: durableIdempotencyKey,
        outcome: selectedOutcome,
        reasonCodes: selectedReasons,
      })
      setCaseData(result)
    } catch (err: any) {
      if (err?.code === 'REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH') {
        setSessionMismatch(true)
        setError(
          'Review session binding mismatch. Your provider session changed since the case was claimed. Use Session Recovery below.'
        )
      } else if (
        err?.code === 'REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT' ||
        err?.code === 'REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED' ||
        err?.code === 'REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED'
      ) {
        setConflictMessage(
          'The case or underlying registration graph state changed. Reloading current state.'
        )
        await loadCase()
      } else if (err?.code === 'REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED') {
        setError(
          'Repair outcome not authorized: The backend revalidated the registration graph under lock and determined this account is not eligible for automated repair.'
        )
      } else {
        setError(err instanceof Error ? err.message : 'Failed to resolve case.')
      }
    } finally {
      setActionBusy(false)
    }
  }

  const isTerminal =
    caseData?.status === 'RESOLVED' ||
    caseData?.status === 'REJECTED' ||
    caseData?.status === 'SECURITY_ESCALATED'

  const canClaim =
    caseData?.status === 'PENDING' ||
    (!caseData?.assigned_to_current_reviewer && !isTerminal)

  const canResolve =
    caseData?.status === 'IN_REVIEW' &&
    caseData?.assigned_to_current_reviewer &&
    !isTerminal

  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ flexGrow: 1, padding: 16 }}>
      <YStack gap="$4" maxWidth={900} width="100%" alignSelf="center">
        <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
          <ActionButton size="$2" onPress={handleBack}>
            ← Back to queue
          </ActionButton>
          <ActionButton
            size="$2"
            disabled={loading || actionBusy}
            onPress={() => void loadCase()}
          >
            Refresh
          </ActionButton>
        </XStack>

        {loading ? (
          <YStack padding="$8" alignItems="center" justifyContent="center">
            <Paragraph color="$nexaSecondary">Loading case details...</Paragraph>
          </YStack>
        ) : !caseData ? (
          <InlineNotice title="Case not found or access denied." tone="danger" />
        ) : (
          <>
            <YStack gap="$2">
              <XStack gap="$2" alignItems="center" flexWrap="wrap">
                <StatusBadge tone="neutral">Case {caseData.case_reference}</StatusBadge>
                <StatusBadge
                  tone={
                    caseData.status === 'RESOLVED'
                      ? 'success'
                      : isTerminal
                        ? 'danger'
                        : 'warning'
                  }
                >
                  {caseData.status}
                </StatusBadge>
                {caseData.assigned_to_current_reviewer ? (
                  <StatusBadge tone="success">Assigned to you</StatusBadge>
                ) : null}
              </XStack>
              <ScreenHeader
                title={`Review Case: ${caseData.case_reference}`}
                description="Clinician adjudication for historical patient registration state."
              />
            </YStack>

            {conflictMessage !== null ? (
              <InlineNotice title={conflictMessage} tone="warning" />
            ) : null}

            {error !== null ? (
              <InlineNotice title={error} tone="danger" />
            ) : null}

            {/* Case Metadata */}
            <YStack
              padding="$4"
              borderRadius="$4"
              backgroundColor="$nexaSurface"
              borderColor="$nexaBorder"
              borderWidth={1}
              gap="$3"
            >
              <Paragraph fontWeight="600" fontSize={15} color="$nexaText">
                Registration State Metadata
              </Paragraph>
              <XStack justifyContent="space-between" flexWrap="wrap" gap="$3">
                <YStack gap="$1">
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Optimistic Version
                  </Paragraph>
                  <Paragraph fontWeight="600" fontSize={14}>
                    v{caseData.version}
                  </Paragraph>
                </YStack>
                <YStack gap="$1">
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Created
                  </Paragraph>
                  <Paragraph fontSize={14}>
                    {new Date(caseData.created_at).toLocaleString()}
                  </Paragraph>
                </YStack>
                {caseData.claimed_at ? (
                  <YStack gap="$1">
                    <Paragraph color="$nexaSecondary" fontSize={12}>
                      Claimed
                    </Paragraph>
                    <Paragraph fontSize={14}>
                      {new Date(caseData.claimed_at).toLocaleString()}
                    </Paragraph>
                  </YStack>
                ) : null}
                {caseData.resolved_at ? (
                  <YStack gap="$1">
                    <Paragraph color="$nexaSecondary" fontSize={12}>
                      Resolved
                    </Paragraph>
                    <Paragraph fontSize={14}>
                      {new Date(caseData.resolved_at).toLocaleString()}
                    </Paragraph>
                  </YStack>
                ) : null}
              </XStack>

              <YStack gap="$2" paddingTop="$2">
                <Paragraph color="$nexaSecondary" fontSize={12}>
                  Reason Codes
                </Paragraph>
                <XStack gap="$2" flexWrap="wrap">
                  {caseData.reason_codes.map((reason) => (
                    <Text
                      key={reason}
                      fontSize={12}
                      paddingHorizontal="$2"
                      paddingVertical="$1"
                      borderRadius="$2"
                      backgroundColor="$nexaCanvas"
                      color="$nexaText"
                      fontWeight="600"
                    >
                      {reason}
                    </Text>
                  ))}
                </XStack>
              </YStack>

              {caseData.outcome ? (
                <YStack gap="$1" paddingTop="$2">
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Resolved Outcome
                  </Paragraph>
                  <Paragraph fontWeight="700" color="$nexaText">
                    {caseData.outcome}
                  </Paragraph>
                </YStack>
              ) : null}
            </YStack>

            {/* Session Recovery Banner if Session Mismatch */}
            {sessionMismatch && !isTerminal ? (
              <YStack
                padding="$4"
                borderRadius="$4"
                backgroundColor="$nexaSurface"
                borderColor="$nexaWarning"
                borderWidth={1}
                gap="$3"
              >
                <Paragraph fontWeight="600" color="$nexaText">
                  Session Re-binding Required
                </Paragraph>
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  The reviewer session token has rotated or re-authenticated since this
                  case was claimed. Perform session recovery to bind your current active
                  session.
                </Paragraph>
                <ActionButton
                  intent="primary"
                  disabled={actionBusy}
                  aria-busy={actionBusy}
                  onPress={() => void handleRecoverSession()}
                >
                  {actionBusy ? 'Recovering session...' : 'Recover review session'}
                </ActionButton>
              </YStack>
            ) : null}

            {/* Claim Action */}
            {canClaim ? (
              <YStack
                padding="$4"
                borderRadius="$4"
                backgroundColor="$nexaSurface"
                borderColor="$nexaBorder"
                borderWidth={1}
                gap="$3"
              >
                <Paragraph fontWeight="600" fontSize={15} color="$nexaText">
                  Claim Case for Adjudication
                </Paragraph>
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  Claiming binds this case to your verified reviewer authority and active
                  hospital affiliation.
                </Paragraph>
                <ActionButton
                  intent="primary"
                  disabled={actionBusy}
                  aria-busy={actionBusy}
                  onPress={() => void handleClaim()}
                >
                  {actionBusy ? 'Claiming...' : 'Claim case'}
                </ActionButton>
              </YStack>
            ) : null}

            {/* Resolve Form */}
            {canResolve ? (
              <YStack
                padding="$4"
                borderRadius="$4"
                backgroundColor="$nexaSurface"
                borderColor="$nexaBorder"
                borderWidth={1}
                gap="$4"
              >
                <YStack gap="$1">
                  <Paragraph fontWeight="700" fontSize={16} color="$nexaText">
                    Adjudication & Terminal Resolution
                  </Paragraph>
                  <Paragraph color="$nexaSecondary" fontSize={13}>
                    The backend revalidates the exact registration graph under an advisory
                    lock before applying any outcome.
                  </Paragraph>
                </YStack>

                {/* Outcome Selection */}
                <YStack gap="$2">
                  <Paragraph fontWeight="600" fontSize={14}>
                    Select Disposition Outcome:
                  </Paragraph>
                  {OUTCOMES.map((item) => {
                    const isSelected = selectedOutcome === item.value
                    return (
                      <YStack
                        key={item.value}
                        padding="$3"
                        borderRadius="$3"
                        backgroundColor={isSelected ? '$nexaCanvas' : '$nexaSurface'}
                        borderColor={isSelected ? '$nexaText' : '$nexaBorder'}
                        borderWidth={1}
                        gap="$1"
                        cursor="pointer"
                        onPress={() => setSelectedOutcome(item.value)}
                        role="radio"
                        aria-checked={isSelected}
                      >
                        <XStack justifyContent="space-between" alignItems="center">
                          <Paragraph fontWeight="600" fontSize={14} color="$nexaText">
                            {item.label}
                          </Paragraph>
                          {item.isRepair ? (
                            <StatusBadge tone="warning">Repair</StatusBadge>
                          ) : (
                            <StatusBadge tone="neutral">No repair</StatusBadge>
                          )}
                        </XStack>
                        <Paragraph color="$nexaSecondary" fontSize={12}>
                          {item.description}
                        </Paragraph>
                      </YStack>
                    )
                  })}
                </YStack>

                {/* Reason Codes Selection */}
                <YStack gap="$2">
                  <Paragraph fontWeight="600" fontSize={14}>
                    Adjudication Reason Codes (select 1 to 4):
                  </Paragraph>
                  <XStack gap="$2" flexWrap="wrap">
                    {ALL_REASONS.map((reason) => {
                      const isSelected = selectedReasons.includes(reason)
                      return (
                        <ActionButton
                          key={reason}
                          size="$2"
                          intent={isSelected ? 'primary' : undefined}
                          onPress={() => toggleReason(reason)}
                        >
                          {reason}
                        </ActionButton>
                      )
                    })}
                  </XStack>
                  {selectedReasons.length === 0 ? (
                    <Paragraph color="$nexaDanger" fontSize={12}>
                      At least one reason code must be selected.
                    </Paragraph>
                  ) : null}
                </YStack>

                {/* Submission CTA */}
                <ActionButton
                  intent="primary"
                  size="$4"
                  disabled={actionBusy || selectedReasons.length === 0}
                  aria-busy={actionBusy}
                  onPress={() => void handleResolve()}
                >
                  {actionBusy ? 'Submitting resolution...' : 'Submit terminal resolution'}
                </ActionButton>
              </YStack>
            ) : null}

            {isTerminal ? (
              <InlineNotice
                tone={caseData.status === 'RESOLVED' ? 'success' : 'danger'}
                title={`Case disposition is terminal: ${caseData.status}`}
                description="This case has concluded and cannot be further modified. The patient may now check their status."
              />
            ) : null}
          </>
        )}
      </YStack>
    </ScrollView>
  )
}
