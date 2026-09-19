'use client'

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
import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  NexaApiClient,
  type TreatmentSessionV1Operation,
  type TreatmentVitalRequest,
} from '../../utils/apiClient'
import {
  TreatmentVitalMutationIntent,
  validateTreatmentVitalRequest,
} from '../../services/treatmentVitalsClient'
import { useProviderAuth } from './ProviderAuthContext'

type FlowState =
  | 'select_patient'
  | 'requesting'
  | 'waiting'
  | 'establishing_encounter'
  | 'ready'
  | 'request_error'
  | 'authority_error'
  | 'trust_error'

type WriteState = 'idle' | 'submitting' | 'success' | 'uncertain' | 'validation_error'

type VitalKind = TreatmentVitalRequest['kind']

const REQUIRED_OPERATIONS: TreatmentSessionV1Operation[] = [
  'CREATE_ENCOUNTER',
  'WRITE_VITALS',
]

function exactOperations(operations: TreatmentSessionV1Operation[]): boolean {
  return (
    operations.length === 2 &&
    operations.includes('CREATE_ENCOUNTER') &&
    operations.includes('WRITE_VITALS')
  )
}

function isTransportUncertain(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 0 || error.status >= 500 || error.isRetryable)
  )
}

function terminalKind(error: unknown): 'operation' | 'trust' | 'authority' | null {
  if (!(error instanceof ApiError)) return null
  if (error.code === 'TREATMENT_OPERATION_NOT_AUTHORIZED') return 'operation'
  if (
    error.status === 401 ||
    error.code === 'REAUTH_REQUIRED' ||
    error.code === 'TREATMENT_PROVIDER_NO_LONGER_ELIGIBLE' ||
    error.code === 'TREATMENT_PROVIDER_SESSION_MISMATCH'
  ) {
    return 'trust'
  }
  if (error.status === 403) return 'authority'
  return null
}

function freshRecordedAt(): string {
  return new Date().toISOString()
}

export function TreatmentVitalsScreen() {
  const router = useRouter()
  const {
    isAuthenticated,
    displayName,
    hospitalName,
    discoverySelection,
    clearDiscoverySelection,
    treatmentSession,
    setTreatmentSession,
    setTreatmentEncounter,
    clearTreatmentSession,
  } = useProviderAuth()

  const [flowState, setFlowState] = useState<FlowState>(
    treatmentSession?.encounterId ? 'ready' : discoverySelection ? 'requesting' : 'select_patient'
  )
  const [requestId, setRequestId] = useState(treatmentSession?.requestId ?? '')
  const [requestExpiresAt, setRequestExpiresAt] = useState<number | null>(null)
  const [patientDisplay, setPatientDisplay] = useState(
    treatmentSession?.patientDisplayIdentifier ?? discoverySelection?.displayIdentifier ?? ''
  )
  const [flowMessage, setFlowMessage] = useState<string | null>(null)
  const [linkCopied, setLinkCopied] = useState(false)

  const [kind, setKind] = useState<VitalKind>('blood_pressure')
  const [systolic, setSystolic] = useState('')
  const [diastolic, setDiastolic] = useState('')
  const [heartRate, setHeartRate] = useState('')
  const [temperature, setTemperature] = useState('')
  const [spo2, setSpo2] = useState('')
  const [recordedAt, setRecordedAt] = useState(freshRecordedAt)
  const [writeState, setWriteState] = useState<WriteState>('idle')
  const [writeMessage, setWriteMessage] = useState<string | null>(null)

  const requestStartedRef = useRef(false)
  const claimInFlightRef = useRef(false)
  const encounterInFlightRef = useRef(false)
  const submitInFlightRef = useRef(false)
  const mutationIntentRef = useRef(new TreatmentVitalMutationIntent())

  const deepLink = requestId
    ? `nexacare://patient/treatment-request?requestId=${encodeURIComponent(requestId)}`
    : ''

  const observation = useMemo<TreatmentVitalRequest | null>(() => {
    if (kind === 'blood_pressure') {
      if (!systolic.trim() || !diastolic.trim()) return null
      return {
        kind,
        systolic_bp: Number(systolic),
        diastolic_bp: Number(diastolic),
        recorded_at: recordedAt,
      }
    }
    if (kind === 'heart_rate') {
      if (!heartRate.trim()) return null
      return {
        kind,
        beats_per_minute: Number(heartRate),
        recorded_at: recordedAt,
      }
    }
    if (kind === 'temperature') {
      if (!temperature.trim()) return null
      return {
        kind,
        celsius: Number(temperature),
        recorded_at: recordedAt,
      }
    }
    if (!spo2.trim()) return null
    return {
      kind: 'spo2',
      percentage: Number(spo2),
      recorded_at: recordedAt,
    }
  }, [diastolic, heartRate, kind, recordedAt, spo2, systolic, temperature])

  const resetWriteStatusOnEdit = useCallback(() => {
    if (writeState !== 'submitting') {
      setWriteState('idle')
      setWriteMessage(null)
    }
  }, [writeState])

  const clearAuthority = useCallback(
    (message: string, nextState: 'authority_error' | 'trust_error') => {
      mutationIntentRef.current.resetForSessionReplacement()
      clearTreatmentSession()
      setFlowState(nextState)
      setFlowMessage(message)
      setWriteState('idle')
      setWriteMessage(null)
    },
    [clearTreatmentSession]
  )

  const establishEncounter = useCallback(
    async (token: string) => {
      if (encounterInFlightRef.current) return
      encounterInFlightRef.current = true
      setFlowState('establishing_encounter')
      setFlowMessage(null)
      try {
        const encounter = await NexaApiClient.createTreatmentSessionEncounter(token)
        setTreatmentEncounter(encounter.encounter_id)
        setFlowState('ready')
      } catch (error) {
        const terminal = terminalKind(error)
        if (terminal === 'trust') {
          clearAuthority(
            'Provider trust or provider-session binding is no longer valid. Sign in again before clinical entry.',
            'trust_error'
          )
        } else if (terminal === 'operation' || terminal === 'authority') {
          clearAuthority(
            'Treatment Session authority is no longer valid. Start a new patient-signed Treatment Session.',
            'authority_error'
          )
        } else {
          setFlowState('request_error')
          setFlowMessage(
            'Encounter establishment was interrupted. The treatment authority remains in memory; retry this step.'
          )
        }
      } finally {
        encounterInFlightRef.current = false
      }
    },
    [clearAuthority, setTreatmentEncounter]
  )

  useEffect(() => {
    if (
      treatmentSession &&
      Date.parse(treatmentSession.expiresAt) <= Date.now()
    ) {
      clearAuthority(
        'Treatment Session expired. A new patient-signed Treatment Session is required.',
        'authority_error'
      )
      return
    }
    if (treatmentSession?.encounterId) {
      setPatientDisplay(treatmentSession.patientDisplayIdentifier)
      setRequestId(treatmentSession.requestId)
      setFlowState('ready')
      return
    }
    if (treatmentSession?.treatmentToken && !treatmentSession.encounterId) {
      setPatientDisplay(treatmentSession.patientDisplayIdentifier)
      setRequestId(treatmentSession.requestId)
      void establishEncounter(treatmentSession.treatmentToken)
    }
  }, [clearAuthority, establishEncounter, treatmentSession])

  useEffect(() => {
    if (
      !isAuthenticated ||
      treatmentSession ||
      !discoverySelection ||
      requestStartedRef.current
    ) {
      return
    }
    requestStartedRef.current = true
    const displayIdentifier = discoverySelection.displayIdentifier
    const handle = discoverySelection.discoveryHandle
    setPatientDisplay(displayIdentifier)
    setFlowState('requesting')
    setFlowMessage(null)

    // Discovery handles are one-time authority. Remove it from UI state as soon
    // as the request attempt starts so no retry can accidentally reuse it.
    clearDiscoverySelection()

    void NexaApiClient.createTreatmentSessionV1Request({
      protocol_version: 'nexa-treatment-session-v1',
      discovery_handle: handle,
      purpose: 'record_vitals',
      allowed_operations: REQUIRED_OPERATIONS,
      access_duration_seconds: 900,
    })
      .then((result) => {
        setRequestId(result.request_id)
        setRequestExpiresAt(Date.now() + result.expires_in_seconds * 1000)
        setFlowState('waiting')
      })
      .catch((error: unknown) => {
        setFlowState('request_error')
        setFlowMessage(
          isTransportUncertain(error)
            ? 'Treatment request outcome is uncertain. Re-identify the patient before creating another request.'
            : 'Treatment request could not be created. Re-identify the patient to try again.'
        )
      })
  }, [
    clearDiscoverySelection,
    discoverySelection,
    isAuthenticated,
    treatmentSession,
  ])

  const claimApprovedSession = useCallback(async () => {
    if (!requestId || claimInFlightRef.current || flowState !== 'waiting') return
    claimInFlightRef.current = true
    try {
      const claim = await NexaApiClient.claimTreatmentSessionV1(requestId)
      if (!exactOperations(claim.allowed_operations)) {
        setFlowState('authority_error')
        setFlowMessage('Approved Treatment Session operation set is not valid for this workflow.')
        return
      }
      const grant = {
        requestId,
        treatmentToken: claim.treatment_token,
        allowedOperations: claim.allowed_operations,
        expiresAt: claim.expires_at,
        patientDisplayIdentifier: patientDisplay || 'Selected patient',
        encounterId: null,
      }
      setTreatmentSession(grant)
      mutationIntentRef.current.resetForSessionReplacement()
      await establishEncounter(claim.treatment_token)
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === 'TREATMENT_NOT_APPROVED'
      ) {
        return
      }
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === 'TREATMENT_SESSION_ALREADY_CLAIMED'
      ) {
        clearAuthority(
          'This approved Treatment Session was already claimed, but its bearer is no longer available in memory. Start a new Treatment Session.',
          'authority_error'
        )
        return
      }
      const terminal = terminalKind(error)
      if (terminal === 'trust') {
        clearAuthority(
          'Provider trust changed while claiming the Treatment Session. Sign in again.',
          'trust_error'
        )
      } else if (terminal === 'authority' || error instanceof ApiError && error.status === 404) {
        clearAuthority(
          'Treatment request expired or is no longer claimable. Start a new Treatment Session.',
          'authority_error'
        )
      } else if (!isTransportUncertain(error)) {
        setFlowState('request_error')
        setFlowMessage('Unable to claim the approved Treatment Session.')
      }
    } finally {
      claimInFlightRef.current = false
    }
  }, [
    clearAuthority,
    establishEncounter,
    flowState,
    patientDisplay,
    requestId,
    setTreatmentSession,
  ])

  useEffect(() => {
    if (flowState !== 'waiting' || !requestId) return
    void claimApprovedSession()
    const timer = setInterval(() => {
      if (requestExpiresAt !== null && Date.now() >= requestExpiresAt) {
        setFlowState('authority_error')
        setFlowMessage('Patient approval window expired. Start a new Treatment Session.')
        return
      }
      void claimApprovedSession()
    }, 2000)
    return () => clearInterval(timer)
  }, [claimApprovedSession, flowState, requestExpiresAt, requestId])

  const submitObservation = useCallback(async () => {
    if (
      submitInFlightRef.current ||
      !treatmentSession?.treatmentToken ||
      !treatmentSession.encounterId
    ) {
      return
    }
    if (!observation) {
      setWriteState('validation_error')
      setWriteMessage('Enter the selected observation value before submitting.')
      return
    }
    const validation = validateTreatmentVitalRequest(observation)
    if (validation) {
      setWriteState('validation_error')
      setWriteMessage(validation)
      return
    }

    let key: string
    try {
      key = mutationIntentRef.current.keyFor(observation)
    } catch {
      setWriteState('validation_error')
      setWriteMessage(
        'This exact observation is already committed. Change the observation or recorded time for a new clinical fact.'
      )
      return
    }

    submitInFlightRef.current = true
    setWriteState('submitting')
    setWriteMessage(null)
    try {
      await NexaApiClient.writeTreatmentSessionVital(
        treatmentSession.treatmentToken,
        key,
        observation
      )
      mutationIntentRef.current.markComplete(observation)
      setWriteState('success')
      setWriteMessage('Observation committed successfully.')
    } catch (error) {
      const terminal = terminalKind(error)
      if (terminal === 'operation') {
        mutationIntentRef.current.clearUncertainIntent()
        clearAuthority(
          'This Treatment Session does not authorize WRITE_VITALS. Start a new correctly scoped session.',
          'authority_error'
        )
      } else if (terminal === 'trust') {
        mutationIntentRef.current.clearUncertainIntent()
        clearAuthority(
          'Provider trust or session binding changed before commit. Sign in again before clinical entry.',
          'trust_error'
        )
      } else if (terminal === 'authority') {
        mutationIntentRef.current.clearUncertainIntent()
        clearAuthority(
          'Treatment Session expired or was revoked. Start a new patient-signed Treatment Session.',
          'authority_error'
        )
      } else if (isTransportUncertain(error)) {
        setWriteState('uncertain')
        setWriteMessage(
          'The server outcome is uncertain. Retry the unchanged observation to reconcile using the same idempotency key.'
        )
      } else {
        mutationIntentRef.current.clearUncertainIntent()
        setWriteState('validation_error')
        setWriteMessage(
          error instanceof ApiError && error.status === 409
            ? 'This idempotency key no longer matches the observation. Review the values before retrying.'
            : 'The observation was rejected. Review the values before retrying.'
        )
      }
    } finally {
      submitInFlightRef.current = false
    }
  }, [clearAuthority, observation, treatmentSession])

  if (!isAuthenticated) {
    return <LoadingState label="Provider session required." />
  }

  if (flowState === 'select_patient') {
    return (
      <ScreenContainer>
        <ScreenHeader
          eyebrow="TREATMENT SESSION"
          title="Record Vitals"
          description="A patient-signed Treatment Session is required before clinical entry."
        />
        <InlineNotice title="No active Treatment Session" tone="warning">
          Signed Consent V3 is read-only and is not a fallback for this clinical write.
        </InlineNotice>
        <ActionButton onPress={() => router.push('/doctor/patient-search?intent=treatment_vitals')}>
          Select Patient
        </ActionButton>
      </ScreenContainer>
    )
  }

  if (flowState === 'requesting') {
    return <LoadingState label="Creating bounded Treatment Session request..." />
  }

  if (flowState === 'waiting') {
    return (
      <ScreenContainer>
        <ScreenHeader
          eyebrow="PATIENT APPROVAL"
          title="Waiting for Treatment Session approval"
          description="The patient must approve exactly CREATE_ENCOUNTER and WRITE_VITALS."
        />
        <Surface gap="$3">
          <Text fontWeight="700">Patient</Text>
          <Paragraph>{patientDisplay || 'Selected patient'}</Paragraph>
          <Text fontWeight="700">Request ID</Text>
          <Paragraph>{requestId}</Paragraph>
          <Text fontWeight="700">Patient app link</Text>
          <Paragraph>{deepLink}</Paragraph>
          <XStack gap="$3" flexWrap="wrap">
            <ActionButton
              accessibilityLabel="Copy patient Treatment Session approval link"
              onPress={() => {
                if (typeof navigator !== 'undefined' && navigator.clipboard) {
                  void navigator.clipboard.writeText(deepLink).then(() => setLinkCopied(true))
                }
              }}
            >
              Copy Approval Link
            </ActionButton>
            <ActionButton
              accessibilityLabel="Check Treatment Session approval now"
              onPress={() => void claimApprovedSession()}
            >
              Check Approval
            </ActionButton>
          </XStack>
          {linkCopied ? (
            <Paragraph accessibilityRole="status">Approval link copied.</Paragraph>
          ) : null}
        </Surface>
        <InlineNotice title="Operation-bound request">
          The request contains no Treatment Session bearer. The bearer is minted only after the
          patient signs and this provider claims the approved request.
        </InlineNotice>
      </ScreenContainer>
    )
  }

  if (flowState === 'establishing_encounter') {
    return <LoadingState label="Establishing canonical Encounter..." />
  }

  if (flowState === 'request_error' || flowState === 'authority_error' || flowState === 'trust_error') {
    const trust = flowState === 'trust_error'
    return (
      <ScreenContainer>
        <ScreenHeader
          eyebrow="TREATMENT SESSION"
          title={trust ? 'Provider trust required' : 'Treatment Session unavailable'}
        />
        <InlineNotice title={flowMessage ?? 'This treatment workflow cannot continue.'} tone="danger" />
        <XStack gap="$3" flexWrap="wrap">
          {treatmentSession?.treatmentToken && flowState === 'request_error' ? (
            <ActionButton onPress={() => void establishEncounter(treatmentSession.treatmentToken)}>
              Retry Encounter Establishment
            </ActionButton>
          ) : null}
          <ActionButton
            onPress={() => {
              clearTreatmentSession()
              mutationIntentRef.current.resetForSessionReplacement()
              router.push(trust ? '/doctor/login' : '/doctor/patient-search?intent=treatment_vitals')
            }}
          >
            {trust ? 'Sign In Again' : 'Start New Treatment Session'}
          </ActionButton>
        </XStack>
      </ScreenContainer>
    )
  }

  const statusTone = writeState === 'success' ? 'success' : writeState === 'uncertain' ? 'warning' : 'info'

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="BOUNDED CLINICAL ENTRY"
        title="Record one vital observation"
        description="Each submission creates one typed observation under the active Treatment Session."
      />

      <Surface gap="$2">
        <XStack gap="$2" flexWrap="wrap">
          <StatusBadge tone="success">Treatment Session active</StatusBadge>
          <StatusBadge tone="success">Encounter established</StatusBadge>
          <StatusBadge tone="info">WRITE_VITALS only</StatusBadge>
        </XStack>
        <Paragraph>Patient: {treatmentSession?.patientDisplayIdentifier}</Paragraph>
        <Paragraph>Provider: {displayName ?? 'Current provider'}</Paragraph>
        <Paragraph>Facility: {hospitalName ?? 'Current facility'}</Paragraph>
        <Paragraph>
          Encounter reference: {treatmentSession?.encounterId?.slice(0, 8) ?? 'unavailable'}…
        </Paragraph>
      </Surface>

      <Surface gap="$4">
        <SectionHeading>Observation type</SectionHeading>
        <XStack gap="$2" flexWrap="wrap">
          {([
            ['blood_pressure', 'Blood pressure'],
            ['heart_rate', 'Heart rate'],
            ['temperature', 'Temperature'],
            ['spo2', 'SpO₂'],
          ] as Array<[VitalKind, string]>).map(([value, label]) => (
            <ActionButton
              key={value}
              intent={kind === value ? 'primary' : undefined}
              accessibilityLabel={`Select ${label} observation`}
              onPress={() => {
                setKind(value)
                resetWriteStatusOnEdit()
              }}
            >
              {label}
            </ActionButton>
          ))}
        </XStack>

        {kind === 'blood_pressure' ? (
          <XStack gap="$3" flexWrap="wrap">
            <YStack flex={1} minWidth={200}>
              <FormField
                id="vital-systolic"
                label="Systolic (mmHg)"
                value={systolic}
                keyboardType="number-pad"
                onChangeText={(value) => {
                  setSystolic(value)
                  resetWriteStatusOnEdit()
                }}
              />
            </YStack>
            <YStack flex={1} minWidth={200}>
              <FormField
                id="vital-diastolic"
                label="Diastolic (mmHg)"
                value={diastolic}
                keyboardType="number-pad"
                onChangeText={(value) => {
                  setDiastolic(value)
                  resetWriteStatusOnEdit()
                }}
              />
            </YStack>
          </XStack>
        ) : null}

        {kind === 'heart_rate' ? (
          <FormField
            id="vital-heart-rate"
            label="Heart rate (bpm)"
            value={heartRate}
            keyboardType="number-pad"
            onChangeText={(value) => {
              setHeartRate(value)
              resetWriteStatusOnEdit()
            }}
          />
        ) : null}

        {kind === 'temperature' ? (
          <FormField
            id="vital-temperature"
            label="Temperature (°C)"
            value={temperature}
            keyboardType="decimal-pad"
            onChangeText={(value) => {
              setTemperature(value)
              resetWriteStatusOnEdit()
            }}
          />
        ) : null}

        {kind === 'spo2' ? (
          <FormField
            id="vital-spo2"
            label="SpO₂ (%)"
            value={spo2}
            keyboardType="decimal-pad"
            onChangeText={(value) => {
              setSpo2(value)
              resetWriteStatusOnEdit()
            }}
          />
        ) : null}

        <FormField
          id="vital-recorded-at"
          label="Observed at (ISO 8601 with timezone)"
          value={recordedAt}
          hint="Example: 2026-09-19T09:00:00Z"
          autoCapitalize="none"
          autoCorrect={false}
          onChangeText={(value) => {
            setRecordedAt(value)
            resetWriteStatusOnEdit()
          }}
        />

        {writeMessage ? (
          <InlineNotice
            title={writeMessage}
            tone={writeState === 'success' ? 'success' : writeState === 'uncertain' ? 'warning' : 'danger'}
          />
        ) : null}

        <XStack alignItems="center" gap="$3" flexWrap="wrap">
          <StatusBadge tone={statusTone}>
            {writeState === 'submitting'
              ? 'Submitting'
              : writeState === 'success'
                ? 'Committed'
                : writeState === 'uncertain'
                  ? 'Outcome uncertain'
                  : 'Ready'}
          </StatusBadge>
          <ActionButton
            intent="primary"
            disabled={writeState === 'submitting'}
            accessibilityLabel={
              writeState === 'uncertain'
                ? 'Retry unchanged vital observation with the same idempotency key'
                : 'Submit one vital observation'
            }
            onPress={() => void submitObservation()}
          >
            {writeState === 'submitting'
              ? 'Submitting...'
              : writeState === 'uncertain'
                ? 'Retry Same Observation'
                : 'Commit Observation'}
          </ActionButton>
        </XStack>
        <Paragraph color="$nexaSecondary">
          This form records measurements only. It does not diagnose, classify normality, or recommend treatment.
        </Paragraph>
      </Surface>
    </ScreenContainer>
  )
}
