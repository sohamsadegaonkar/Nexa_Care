'use client'

import {
  Button,
  Card,
  H2,
  Input,
  Paragraph,
  ScrollView,
  Separator,
  Spinner,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { useParams, useRouter } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  NexaApiClient,
  type AdjudicatedClinicalField,
  type AdjudicationOutcome,
  type AdjudicationReasonCode,
} from '../../utils/apiClient'
import {
  clearAdjudicationWorkflow,
  prepareAdjudicationMutation,
  recordAdjudicationSubmission,
  useAdjudicationWorkflow,
} from '../../services/adjudicationWorkflowStore'
import { useProviderAuth } from '../doctor/ProviderAuthContext'
import {
  REASONS_BY_OUTCOME,
  VITAL_TYPES,
  adjudicationFingerprint,
  type ClinicalEntryDraft,
  validateClinicalEntry,
  validateReasonCodes,
} from './adjudicationContract'
import { ProtectedSourceViewer } from './ProtectedSourceViewer'

const INITIAL_DRAFT: ClinicalEntryDraft = {
  kind: 'VITAL',
  vitalType: 'HEART_RATE',
  testName: '',
  numericValue: '',
  unit: '',
  referenceRange: '',
  isAbnormal: false,
  effectiveAt: '',
  pageNumber: '',
  provenanceType: 'HUMAN_VERIFIED',
}

const OUTCOMES: Array<{ value: AdjudicationOutcome; label: string }> = [
  { value: 'ACCEPTED', label: 'Accept verified information' },
  { value: 'REJECTED', label: 'Reject source item' },
  { value: 'NEEDS_SPECIALIST_REVIEW', label: 'Request specialist review' },
]

function safeActionError(reason: unknown): { message: string; terminal: boolean } {
  if (!(reason instanceof ApiError)) {
    return { message: 'The submission could not be completed. Try again safely.', terminal: false }
  }
  const terminalCodes = new Set([
    'ADJUDICATION_SESSION_MISMATCH',
    'ADJUDICATION_CONSENT_INACTIVE',
    'ADJUDICATION_ACCESS_DENIED',
    'ADJUDICATION_VERSION_UNSUPPORTED',
    'ADJUDICATION_ERASURE_ACCESS_BLOCKED',
    'ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE',
  ])
  if (terminalCodes.has(reason.code ?? '') || reason.status === 403) {
    return {
      message: 'Review access is no longer valid. Reopen the authorized workflow.',
      terminal: true,
    }
  }
  if (reason.code === 'ADJUDICATION_IDEMPOTENCY_COLLISION') {
    return {
      message:
        'This request conflicts with an earlier operation. Review the case before continuing.',
      terminal: false,
    }
  }
  if (
    reason.code === 'ADJUDICATION_ALREADY_RESOLVED' ||
    reason.code === 'ADJUDICATION_CASE_CONFLICT'
  ) {
    return { message: 'This case changed and can no longer accept that action.', terminal: false }
  }
  return {
    message: 'The submission was not accepted. Review the form and try again.',
    terminal: false,
  }
}

export function AdjudicationReviewScreen() {
  const params = useParams<{ caseId: string }>()
  const caseId = String(params.caseId)
  const router = useRouter()
  const { hydrated, isAuthenticated, roles } = useProviderAuth()
  const workflow = useAdjudicationWorkflow(caseId)
  const [draft, setDraft] = useState<ClinicalEntryDraft>(INITIAL_DRAFT)
  const [outcome, setOutcome] = useState<AdjudicationOutcome>('ACCEPTED')
  const [reasons, setReasons] = useState<AdjudicationReasonCode[]>(['SOURCE_VERIFIED'])
  const [confirmation, setConfirmation] = useState<AdjudicatedClinicalField[] | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const clinicallyQualified = roles.some((role) =>
    ['clinician', 'clinical_reviewer'].includes(role)
  )

  useEffect(() => {
    if (hydrated && !isAuthenticated) router.replace('/doctor/login')
  }, [hydrated, isAuthenticated, router])

  const clearTerminal = useCallback(() => {
    clearAdjudicationWorkflow(caseId)
  }, [caseId])

  const allowedReasons = REASONS_BY_OUTCOME[outcome]
  const selectedReasonSet = useMemo(() => new Set(reasons), [reasons])

  const chooseOutcome = (next: AdjudicationOutcome) => {
    if (submitting) return
    setOutcome(next)
    setReasons([])
    setConfirmation(null)
    setError(null)
  }

  const toggleReason = (reason: AdjudicationReasonCode) => {
    if (submitting) return
    setReasons((current) => {
      if (current.includes(reason)) return current.filter((item) => item !== reason)
      if (current.length >= 4) return current
      return [...current, reason]
    })
    setConfirmation(null)
  }

  const prepareConfirmation = () => {
    setError(null)
    if (!validateReasonCodes(outcome, reasons)) {
      setError('Select only the permitted reason codes for this outcome.')
      return
    }
    if (outcome !== 'ACCEPTED') {
      setConfirmation([])
      return
    }
    const validation = validateClinicalEntry(draft)
    if (!validation.ok) {
      setError(validation.message)
      return
    }
    setConfirmation([validation.field])
  }

  const submit = async () => {
    if (!workflow || !confirmation || submitting) return
    const fingerprint = adjudicationFingerprint(outcome, confirmation, reasons)
    const idempotencyKey = prepareAdjudicationMutation(caseId, fingerprint)
    setSubmitting(true)
    setError(null)
    try {
      const submission = await NexaApiClient.submitAdjudication(caseId, {
        review_session_id: workflow.reviewSessionId,
        idempotency_key: idempotencyKey,
        outcome,
        fields: confirmation,
        reason_codes: reasons,
      })
      recordAdjudicationSubmission(caseId, submission)
      router.replace(`/doctor/pipeline/adjudication/${encodeURIComponent(caseId)}/result`)
    } catch (reason) {
      const safe = safeActionError(reason)
      if (safe.terminal) clearAdjudicationWorkflow(caseId)
      setError(safe.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (!hydrated || !isAuthenticated) return <Spinner size="large" />
  if (!clinicallyQualified) {
    return (
      <YStack
        padding="$6"
        gap="$3"
        role="alert"
      >
        <H2>Clinical review unavailable</H2>
        <Paragraph>Your current role cannot enter or commit clinical information.</Paragraph>
        <Button onPress={() => router.replace('/doctor/pipeline/adjudication')}>
          Back to cases
        </Button>
      </YStack>
    )
  }
  if (!workflow) {
    return (
      <YStack
        padding="$6"
        gap="$3"
        role="alert"
      >
        <H2>Review session unavailable</H2>
        <Paragraph>
          This browser no longer holds the authoritative review session. Reopen the case through the
          authorized workflow. A new session was not created.
        </Paragraph>
        <Button onPress={() => router.replace('/doctor/pipeline/adjudication')}>
          Back to cases
        </Button>
      </YStack>
    )
  }

  return (
    <ScrollView backgroundColor="$background">
      <YStack
        padding="$4"
        gap="$4"
        maxWidth={1400}
        width="100%"
        marginHorizontal="auto"
      >
        <H2>Verify archived source</H2>
        <Paragraph fontWeight="700">
          Enter only information you can directly verify in the source document.
        </Paragraph>
        <XStack
          gap="$4"
          alignItems="flex-start"
          flexWrap="wrap"
        >
          <Card
            borderWidth={1}
            padding="$3"
            flex={1}
            minWidth={320}
          >
            <ProtectedSourceViewer
              caseId={caseId}
              reviewSessionId={workflow.reviewSessionId}
              onTerminalAccessFailure={clearTerminal}
            />
          </Card>
          <YStack
            flex={1}
            minWidth={320}
            gap="$4"
          >
            <Card
              borderWidth={1}
              padding="$4"
              gap="$3"
            >
              <Text fontWeight="700">Human adjudication</Text>
              <Paragraph size="$2">
                Human verification is separate from AI extraction, authorization consent, and the
                later clinical commit.
              </Paragraph>
              <Text fontWeight="700">Outcome</Text>
              {OUTCOMES.map((item) => (
                <Button
                  key={item.value}
                  theme={outcome === item.value ? 'blue' : undefined}
                  disabled={submitting}
                  onPress={() => chooseOutcome(item.value)}
                  aria-pressed={outcome === item.value}
                >
                  {item.label}
                </Button>
              ))}
            </Card>

            {outcome === 'ACCEPTED' ? (
              <Card
                borderWidth={1}
                padding="$4"
                gap="$3"
              >
                <Text fontWeight="700">Structured clinical field</Text>
                <XStack gap="$2">
                  <Button
                    flex={1}
                    aria-pressed={draft.kind === 'VITAL'}
                    onPress={() => setDraft((value) => ({ ...value, kind: 'VITAL' }))}
                  >
                    Vital result
                  </Button>
                  <Button
                    flex={1}
                    aria-pressed={draft.kind === 'LAB_RESULT'}
                    onPress={() => setDraft((value) => ({ ...value, kind: 'LAB_RESULT' }))}
                  >
                    Laboratory result
                  </Button>
                </XStack>
                {draft.kind === 'VITAL' ? (
                  <YStack gap="$2">
                    <Text>Vital type</Text>
                    {VITAL_TYPES.map((vitalType) => (
                      <Button
                        key={vitalType}
                        size="$2"
                        aria-pressed={draft.vitalType === vitalType}
                        onPress={() => setDraft((value) => ({ ...value, vitalType }))}
                      >
                        {vitalType.replaceAll('_', ' ')}
                      </Button>
                    ))}
                  </YStack>
                ) : (
                  <>
                    <Input
                      aria-label="Laboratory test name"
                      placeholder="Laboratory test name"
                      value={draft.testName}
                      onChangeText={(testName) => setDraft((value) => ({ ...value, testName }))}
                    />
                    <Input
                      aria-label="Reference range"
                      placeholder="Reference range"
                      value={draft.referenceRange}
                      onChangeText={(referenceRange) =>
                        setDraft((value) => ({ ...value, referenceRange }))
                      }
                    />
                    <Button
                      aria-pressed={draft.isAbnormal}
                      onPress={() =>
                        setDraft((value) => ({ ...value, isAbnormal: !value.isAbnormal }))
                      }
                    >
                      {draft.isAbnormal ? 'Marked abnormal' : 'Not marked abnormal'}
                    </Button>
                  </>
                )}
                <Input
                  aria-label="Numeric value"
                  inputMode="decimal"
                  placeholder="Numeric value"
                  value={draft.numericValue}
                  onChangeText={(numericValue) => setDraft((value) => ({ ...value, numericValue }))}
                />
                <Input
                  aria-label="Unit"
                  placeholder="Unit"
                  value={draft.unit}
                  onChangeText={(unit) => setDraft((value) => ({ ...value, unit }))}
                />
                <Input
                  aria-label="Observation date and time"
                  type="datetime-local"
                  value={draft.effectiveAt}
                  onChangeText={(effectiveAt) => setDraft((value) => ({ ...value, effectiveAt }))}
                />
                <Input
                  aria-label="Page number if known"
                  inputMode="numeric"
                  placeholder="Page number if known"
                  value={draft.pageNumber}
                  onChangeText={(pageNumber) => setDraft((value) => ({ ...value, pageNumber }))}
                />
                <Button
                  aria-pressed={draft.provenanceType === 'HUMAN_VERIFIED'}
                  onPress={() =>
                    setDraft((value) => ({
                      ...value,
                      provenanceType:
                        value.provenanceType === 'HUMAN_VERIFIED'
                          ? 'HUMAN_TRANSCRIBED'
                          : 'HUMAN_VERIFIED',
                    }))
                  }
                >
                  {draft.provenanceType === 'HUMAN_VERIFIED'
                    ? 'Human verified'
                    : 'Human transcribed'}
                </Button>
              </Card>
            ) : null}

            <Card
              borderWidth={1}
              padding="$4"
              gap="$2"
            >
              <Text fontWeight="700">Safe reason codes</Text>
              {allowedReasons.map((reason) => (
                <Button
                  key={reason}
                  size="$2"
                  aria-pressed={selectedReasonSet.has(reason)}
                  onPress={() => toggleReason(reason)}
                >
                  {reason.replaceAll('_', ' ')}
                </Button>
              ))}
            </Card>

            {error ? (
              <Paragraph
                color="$red10"
                role="alert"
              >
                {error}
              </Paragraph>
            ) : null}

            {confirmation === null ? (
              <Button
                theme="blue"
                disabled={submitting}
                onPress={prepareConfirmation}
              >
                Review submission
              </Button>
            ) : (
              <Card
                borderWidth={1}
                padding="$4"
                gap="$2"
              >
                <Text fontWeight="700">Confirm human verification</Text>
                {confirmation.map((field) => (
                  <YStack
                    key={field.kind}
                    gap="$1"
                  >
                    <Paragraph>
                      Category: {field.kind === 'VITAL' ? 'Vital result' : 'Laboratory result'}
                    </Paragraph>
                    <Paragraph>
                      Value: {field.normalized_value} {field.unit}
                    </Paragraph>
                    <Paragraph>
                      Effective date: {new Date(field.effective_at).toLocaleString()}
                    </Paragraph>
                  </YStack>
                ))}
                <Paragraph>Reason codes: {reasons.join(', ') || 'None selected'}</Paragraph>
                <Paragraph>
                  I verified this information directly against the displayed archived source.
                </Paragraph>
                <Separator />
                <Button
                  theme="blue"
                  disabled={submitting}
                  onPress={() => void submit()}
                >
                  {submitting ? 'Submitting…' : 'Submit adjudication'}
                </Button>
                <Button
                  disabled={submitting}
                  onPress={() => setConfirmation(null)}
                >
                  Back to edit
                </Button>
              </Card>
            )}
          </YStack>
        </XStack>
      </YStack>
    </ScrollView>
  )
}
