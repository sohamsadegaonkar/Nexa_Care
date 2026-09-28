'use client'

import {
  Button,
  Card,
  Input,
  Paragraph,
  ScrollView,
  Select,
  Spinner,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { AlertTriangle, ChevronDown, Phone, QrCode, RadioReceiver, Search } from '@tamagui/lucide-icons'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { discoverPatientExact, type DiscoveryIdentifierType } from '../../services/patientDiscovery'
import { NfcResolveError, resolveNfcCard } from '../../services/nfcResolve'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { BREAK_GLASS_REASON_OPTIONS, type BreakGlassReasonCode } from '../../api/consent'
import { useProviderAuth } from './ProviderAuthContext'

const MIN_JUSTIFICATION_LENGTH = 20
const OTHER_JUSTIFICATION_LENGTH = 80
const MAX_JUSTIFICATION_LENGTH = 500
const REASON_OPTIONS = BREAK_GLASS_REASON_OPTIONS

type EmergencySearchMode = 'public_id' | 'phone' | 'qr' | 'nfc'

function identifierType(mode: Exclude<EmergencySearchMode, 'nfc'>): DiscoveryIdentifierType {
  if (mode === 'phone') return 'PHONE'
  if (mode === 'qr') return 'QR_PUBLIC_ID'
  return 'NEXA_PUBLIC_ID'
}

function validateJustification(reasonCode: BreakGlassReasonCode, value: string): string | null {
  const clean = value.trim()
  const minimumLength =
    reasonCode === 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY'
      ? OTHER_JUSTIFICATION_LENGTH
      : MIN_JUSTIFICATION_LENGTH
  if (clean.length < minimumLength) {
    return `A ${minimumLength}-character clinical justification is required.`
  }
  if (clean.length > MAX_JUSTIFICATION_LENGTH) {
    return `Clinical justification must not exceed ${MAX_JUSTIFICATION_LENGTH} characters.`
  }
  return null
}

function patientSearchFailure(error: unknown): string {
  if (error instanceof NfcResolveError) {
    if (error.status === 429) return 'NFC lookup is temporarily limited. Wait briefly and try again.'
    if (error.retryable) return 'NFC lookup is temporarily unavailable. Try another patient search method or retry when the service recovers.'
    return 'The NFC card could not be matched. Check the card or use another patient search method.'
  }
  if (!(error instanceof ApiError)) {
    return 'Patient search could not be completed. Check the details or try another method.'
  }
  if (error.code === 'CLINICAL_ELIGIBILITY_DENIED') {
    return 'Emergency patient search is not currently authorized for this provider account. Review provider verification status or contact your clinical administrator.'
  }
  if (
    error.code === 'DISCOVERY_RECENT_MFA_REQUIRED' ||
    error.code === 'CLINICAL_MFA_REQUIRED' ||
    error.code === 'RECENT_MFA_REQUIRED'
  ) {
    return 'Additional verification is required before using this search method. Complete MFA and try again.'
  }
  if (error.code === 'DISCOVERY_NO_MATCH') {
    return 'No patient could be matched with the information provided. Check the details or use another search method.'
  }
  if (error.code === 'DISCOVERY_RATE_LIMITED' || error.status === 429) {
    return 'Patient search is temporarily limited after repeated attempts. Wait briefly, then try again.'
  }
  if (error.status >= 500 || error.status === 0) {
    return 'Patient search is temporarily unavailable because a required service cannot be reached.'
  }
  if (error.status === 401) return 'Your provider session expired. Sign in again before continuing.'
  if (error.status === 403) {
    return 'Your current clinical session is not authorized for emergency patient search. Re-authenticate or contact your clinical administrator.'
  }
  return 'Patient search could not be completed. Check the details or try another method.'
}

export function EmergencyAccessScreen() {
  const router = useRouter()
  const { isAuthenticated, session, setAccessGrant } = useProviderAuth()
  const [mode, setMode] = useState<EmergencySearchMode>('public_id')
  const [identifier, setIdentifier] = useState('')
  const [patientSelection, setPatientSelection] = useState<{ handle: string; label: string } | null>(null)
  const [reasonCode, setReasonCode] = useState<BreakGlassReasonCode>('LIFE_THREATENING_EMERGENCY')
  const [justification, setJustification] = useState('')
  const [mfaCode, setMfaCode] = useState('')
  const [needsStepUp, setNeedsStepUp] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isAuthenticated) {
    return (
      <YStack flex={1} justify="center" items="center" gap="$4">
        <Text>Provider session required.</Text>
        <Button onPress={() => router.push('/doctor/login')}>Go to login</Button>
      </YStack>
    )
  }

  const resetPatient = () => {
    setPatientSelection(null)
    setIdentifier('')
    setNeedsStepUp(false)
    setError(null)
  }

  const identifyPatient = async () => {
    const input = identifier.trim()
    const hospitalId = session?.hospital.hospital_id
    if (!input || !hospitalId) {
      setError('Enter or scan patient information before continuing.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const resolved =
        mode === 'nfc'
          ? await resolveNfcCard(input)
          : await discoverPatientExact({ identifier_type: identifierType(mode), value: input }, hospitalId)
      const labels: Record<EmergencySearchMode, string> = {
        public_id: 'Nexa Patient ID',
        phone: 'Phone',
        qr: 'Nexa QR',
        nfc: 'NFC card',
      }
      setPatientSelection({ handle: resolved.discovery_handle, label: labels[mode] })
      setIdentifier('')
    } catch (caught) {
      setError(patientSearchFailure(caught))
    } finally {
      setSubmitting(false)
    }
  }

  const issueEmergencyAccess = async () => {
    if (!patientSelection) {
      setError('Identify the patient before requesting emergency access.')
      return
    }
    const cleanJustification = justification.trim()
    const validationError = validateJustification(reasonCode, cleanJustification)
    if (validationError) {
      setError(validationError)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const result = await NexaApiClient.breakGlassDiscoveredIssue({
        discovery_handle: patientSelection.handle,
        reason_code: reasonCode,
        justification: cleanJustification,
        purpose: 'EMERGENCY',
      })
      setAccessGrant({
        requestId: result.authorization_ref,
        patientId: result.patient_id,
        consentToken: result.consent_token,
        purpose: 'EMERGENCY',
        scope: 'clinical',
        expiresAt: result.expires_at,
      })
      setJustification('')
      setMfaCode('')
      router.push('/doctor/patient-record')
    } catch (caught: unknown) {
      if (caught instanceof ApiError && (caught.status === 428 || caught.code === 'BREAK_GLASS_STEP_UP_MFA_REQUIRED')) {
        setNeedsStepUp(true)
        setError('Additional verification is required for emergency access. Enter your authenticator code to continue.')
      } else if (caught instanceof ApiError && caught.code === 'DISCOVERY_HANDLE_INVALID') {
        setPatientSelection(null)
        setNeedsStepUp(false)
        setError('The patient selection expired. Identify the patient again before continuing.')
      } else if (caught instanceof ApiError && caught.code === 'CLINICAL_ELIGIBILITY_DENIED') {
        setError('Emergency clinical access is not currently authorized for this provider account. Review provider verification status or contact your clinical administrator.')
      } else if (caught instanceof ApiError && caught.status === 429) {
        setError('Emergency access attempts are temporarily limited. Continue urgent care procedures and retry when the limit clears.')
      } else if (caught instanceof ApiError && caught.status === 409) {
        setError('A matching emergency request was just issued. Wait briefly before retrying.')
      } else if (caught instanceof ApiError && caught.status >= 500) {
        setError('Nexa Care emergency authorization is temporarily unavailable. Follow your facility emergency procedure and retry when service recovers.')
      } else if (caught instanceof ApiError && caught.status === 403) {
        setError('This emergency action is not authorized in your current clinical session. Re-authenticate or contact your clinical administrator.')
      } else {
        setError('Emergency access could not be issued. Follow your facility emergency procedure and retry when appropriate.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const verifyStepUp = async () => {
    if (!/^\d{6}$/.test(mfaCode)) {
      setError('Enter a valid 6-digit authenticator code.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await NexaApiClient.verifyActionMfa(mfaCode)
      setNeedsStepUp(false)
      setMfaCode('')
      await issueEmergencyAccess()
    } catch {
      setError('Authenticator verification failed or expired. Enter a new code and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ScrollView>
      <YStack p="$5" gap="$5" maxW={700} mx="auto">
        <XStack gap="$3" items="center">
          <YStack padding="$2.5" borderRadius={12} backgroundColor="$nexaDangerSoft">
            <AlertTriangle size={36} color="$red10" />
          </YStack>
          <YStack gap="$0.5">
            <Text fontSize={26} fontWeight="900" color="$red10">Emergency Access</Text>
            <Text color="$nexaSecondary" fontSize={14}>
              Identify the patient, record the emergency reason, and request time-limited access.
            </Text>
          </YStack>
        </XStack>

        <Card bg="$nexaDangerSoft" borderWidth={1} borderColor="$red10" borderRadius={14} p="$4">
          <Paragraph color="$red10" fontSize={13} lineHeight={20}>
            Emergency access is permanently recorded in the audit trail, time limited, rate limited, and may trigger patient and compliance notifications. Unauthorized or non-emergency use is a compliance violation.
          </Paragraph>
        </Card>

        {!patientSelection ? (
          <YStack gap="$4">
            <Text color="$nexaText" fontWeight="800" fontSize={17}>1. Identify Patient</Text>
            <XStack gap="$2" flexWrap="wrap">
              {([
                ['public_id', 'Nexa ID', Search],
                ['phone', 'Phone', Phone],
                ['qr', 'QR', QrCode],
                ['nfc', 'NFC', RadioReceiver],
              ] as const).map(([value, label, Icon]) => (
                <Button
                  key={value}
                  theme={mode === value ? 'blue' : undefined}
                  onPress={() => {
                    setMode(value)
                    setIdentifier('')
                    setError(null)
                  }}
                >
                  <XStack gap="$2" items="center"><Icon size={16} /><Text>{label}</Text></XStack>
                </Button>
              ))}
            </XStack>
            <Input
              value={identifier}
              onChangeText={(value) => {
                setIdentifier(value)
                if (error) setError(null)
              }}
              placeholder={
                mode === 'public_id'
                  ? 'Nexa Patient ID'
                  : mode === 'phone'
                    ? 'Patient phone number'
                    : mode === 'qr'
                      ? 'Scan or paste Nexa QR'
                      : 'Scan or enter NFC card UID'
              }
              minHeight={50}
              borderRadius={10}
              borderColor="$nexaBorder"
              backgroundColor="$nexaSurface"
              autoCapitalize={mode === 'public_id' ? 'characters' : 'none'}
            />
            <Button theme="blue" onPress={identifyPatient} disabled={submitting || !identifier.trim()}>
              {submitting ? <Spinner /> : 'Identify Patient'}
            </Button>
          </YStack>
        ) : (
          <Card p="$4" borderWidth={1} borderColor="$nexaSuccess" backgroundColor="$nexaSuccessSoft" gap="$2">
            <Text color="$nexaSuccess" fontWeight="800">Patient identified using {patientSelection.label}</Text>
            <Paragraph color="$nexaText">Continue with the emergency reason and clinical justification.</Paragraph>
            <Button chromeless onPress={resetPatient}>Choose a different patient</Button>
          </Card>
        )}

        {patientSelection && (
          <>
            <YStack gap="$2">
              <Text color="$nexaText" fontWeight="800" fontSize={17}>2. Emergency Reason</Text>
              <Select value={reasonCode} onValueChange={(value) => setReasonCode(value as BreakGlassReasonCode)}>
                <Select.Trigger iconAfter={ChevronDown} minHeight={50} borderRadius={10} borderColor="$nexaBorder" backgroundColor="$nexaSurface">
                  <Select.Value />
                </Select.Trigger>
                <Select.Content zIndex={200000}>
                  <Select.Viewport unstyled minWidth={280} maxHeight={320} backgroundColor="$background" borderWidth={1} borderColor="$borderColor" borderRadius="$4" padding="$1">
                    <Select.Group>
                      {REASON_OPTIONS.map((option, index) => (
                        <Select.Item key={option.value} index={index} value={option.value}>
                          <Select.ItemText>{option.label}</Select.ItemText>
                        </Select.Item>
                      ))}
                    </Select.Group>
                  </Select.Viewport>
                </Select.Content>
              </Select>
            </YStack>

            <YStack gap="$2">
              <Text color="$nexaText" fontWeight="700">Clinical Justification</Text>
              <Input
                value={justification}
                onChangeText={setJustification}
                placeholder="Describe why emergency access is clinically necessary"
                multiline
                numberOfLines={4}
                minHeight={100}
                maxLength={MAX_JUSTIFICATION_LENGTH}
                borderColor="$nexaBorder"
                backgroundColor="$nexaSurface"
              />
              <Text color="$nexaSecondary" fontSize={12}>{justification.length}/{MAX_JUSTIFICATION_LENGTH}</Text>
            </YStack>

            {needsStepUp && (
              <Card p="$4" gap="$3" borderWidth={1} borderColor="$nexaWarning" backgroundColor="$nexaWarningSoft">
                <Text fontWeight="800" color="$nexaWarning">Additional verification</Text>
                <Paragraph>Enter the current 6-digit authenticator code to continue emergency access.</Paragraph>
                <Input value={mfaCode} onChangeText={setMfaCode} placeholder="000000" keyboardType="numeric" maxLength={6} secureTextEntry />
                <Button theme="blue" onPress={verifyStepUp} disabled={submitting}>
                  Verify and Continue
                </Button>
              </Card>
            )}

            {!needsStepUp && (
              <Button theme="red" size="$5" onPress={issueEmergencyAccess} disabled={submitting || !justification.trim()}>
                {submitting ? <Spinner /> : 'Request Emergency Access'}
              </Button>
            )}
          </>
        )}

        {error && (
          <Card p="$3" backgroundColor="$nexaDangerSoft" borderLeftWidth={4} borderLeftColor="$nexaDanger">
            <Text color="$nexaDanger" fontWeight="700">{error}</Text>
          </Card>
        )}

        <Button chromeless onPress={() => router.push('/doctor/dashboard')}>Cancel</Button>
      </YStack>
    </ScrollView>
  )
}
