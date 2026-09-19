import { useLocalSearchParams, useRouter } from 'expo-router'
import {
  ActionButton,
  FormField,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenContainer,
  ScreenHeader,
  SectionHeading,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { useEffect, useState } from 'react'
import {
  approveTreatmentSessionWithBiometric,
  classifyTreatmentSessionApprovalError,
  denyTreatmentSessionWithSignature,
  fetchTreatmentSessionChallenge,
  isTreatmentSessionChallengeExpired,
} from '../../services/treatmentSessionSigning'
import type { TreatmentSessionV1Challenge } from '../../utils/apiClient'

interface TreatmentSessionRequestScreenProps {
  initialChallenge?: TreatmentSessionV1Challenge
  requestIdOverride?: string
}

export default function TreatmentSessionRequestScreen({
  initialChallenge,
  requestIdOverride,
}: TreatmentSessionRequestScreenProps) {
  const router = useRouter()
  const params = useLocalSearchParams<{ requestId?: string }>()
  const routedRequestId = requestIdOverride ?? params.requestId ?? ''
  const [manualRequestId, setManualRequestId] = useState(routedRequestId)
  const [challenge, setChallenge] = useState<TreatmentSessionV1Challenge | null>(
    initialChallenge ?? null
  )
  const [loading, setLoading] = useState(Boolean(routedRequestId) && !initialChallenge)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<'approved' | 'denied' | null>(null)

  const loadRequest = async (requestId: string) => {
    const clean = requestId.trim()
    if (!clean) {
      setError('Enter the Treatment Session request ID shown by your provider.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const result = await fetchTreatmentSessionChallenge(clean)
      if (isTreatmentSessionChallengeExpired(result)) {
        setChallenge(null)
        setError('This Treatment Session request has expired.')
        return
      }
      setChallenge(result)
      setManualRequestId(clean)
    } catch (caught) {
      const mapped = classifyTreatmentSessionApprovalError(caught)
      setChallenge(null)
      setError(mapped.message)
      if (mapped.kind === 'reauth') router.replace('/patient/login')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!initialChallenge && routedRequestId) void loadRequest(routedRequestId)
    // The route/request id is immutable for one mounted review surface.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialChallenge, routedRequestId])

  const approve = async () => {
    if (!challenge || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await approveTreatmentSessionWithBiometric(challenge)
      setOutcome('approved')
    } catch (caught) {
      const mapped = classifyTreatmentSessionApprovalError(caught)
      setError(mapped.message)
      if (mapped.kind === 'reauth') router.replace('/patient/login')
    } finally {
      setSubmitting(false)
    }
  }

  const deny = async () => {
    if (!challenge || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await denyTreatmentSessionWithSignature(challenge)
      setOutcome('denied')
    } catch (caught) {
      const mapped = classifyTreatmentSessionApprovalError(caught)
      setError(mapped.message)
      if (mapped.kind === 'reauth') router.replace('/patient/login')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <LoadingState label="Loading Treatment Session request..." />

  if (outcome) {
    return (
      <ScreenContainer>
        <ScreenHeader
          eyebrow="TREATMENT SESSION"
          title={outcome === 'approved' ? 'Treatment Session approved' : 'Treatment Session denied'}
        />
        <InlineNotice
          title={
            outcome === 'approved'
              ? 'Your signed approval was submitted. The provider may now claim only the operations shown in the request.'
              : 'No Treatment Session authority was granted.'
          }
          tone={outcome === 'approved' ? 'success' : 'warning'}
        />
        <ActionButton onPress={() => router.replace('/patient/treatment-access')}>
          View Treatment Access
        </ActionButton>
      </ScreenContainer>
    )
  }

  if (!challenge) {
    return (
      <ScreenContainer>
        <ScreenHeader
          eyebrow="TREATMENT SESSION"
          title="Review a provider treatment request"
          description="Enter the request ID shown by your provider. No treatment bearer token is entered here."
        />
        <FormField
          id="treatment-session-request-id"
          label="Treatment Session request ID"
          value={manualRequestId}
          autoCapitalize="none"
          autoCorrect={false}
          onChangeText={(value) => {
            setManualRequestId(value)
            setError(null)
          }}
          onSubmitEditing={() => void loadRequest(manualRequestId)}
        />
        {error ? <InlineNotice title={error} tone="danger" /> : null}
        <ActionButton onPress={() => void loadRequest(manualRequestId)}>
          Review Request
        </ActionButton>
      </ScreenContainer>
    )
  }

  const minutes = Math.ceil(challenge.access_duration / 60)

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="OPERATION-BOUND APPROVAL"
        title="Treatment Session Request"
        description="Approve only if you recognize this provider and the exact requested operations."
      />

      <Surface gap="$3">
        <SectionHeading>Requesting provider</SectionHeading>
        <Text fontWeight="800">{challenge.provider_name}</Text>
        <Paragraph>{challenge.hospital_name}</Paragraph>
        <Paragraph>Purpose: {challenge.purpose}</Paragraph>
        <Paragraph>
          Access duration after approval: {minutes} minute{minutes === 1 ? '' : 's'}
        </Paragraph>
      </Surface>

      <Surface gap="$3">
        <SectionHeading>Exact operations requested</SectionHeading>
        <XStack gap="$2" flexWrap="wrap">
          {challenge.allowed_operations.map((operation) => (
            <StatusBadge key={operation} tone="info">
              {operation}
            </StatusBadge>
          ))}
        </XStack>
        <InlineNotice title="What this request allows">
          CREATE_ENCOUNTER establishes the server-owned treatment context. WRITE_VITALS permits one
          bounded vitals mutation at a time. This approval does not authorize prescriptions,
          diagnoses, clinical notes, investigations, allergies, or document mutation.
        </InlineNotice>
      </Surface>

      {error ? <InlineNotice title={error} tone="danger" /> : null}

      <YStack gap="$3">
        <ActionButton
          intent="primary"
          disabled={submitting}
          accessibilityLabel="Approve Treatment Session with biometrics"
          onPress={() => void approve()}
        >
          {submitting ? 'Confirming...' : 'Approve with Biometrics'}
        </ActionButton>
        <ActionButton
          intent="danger"
          disabled={submitting}
          accessibilityLabel="Deny Treatment Session request"
          onPress={() => void deny()}
        >
          Deny
        </ActionButton>
      </YStack>

      <Paragraph color="$nexaSecondary">
        Security context, signatures, and device-binding values are verified in the background and
        are never displayed or copied from this screen.
      </Paragraph>
    </ScreenContainer>
  )
}
