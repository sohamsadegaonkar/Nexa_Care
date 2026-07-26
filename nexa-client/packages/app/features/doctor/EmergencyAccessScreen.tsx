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
import { AlertTriangle, ChevronDown } from '@tamagui/lucide-icons'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { BREAK_GLASS_REASON_OPTIONS, type BreakGlassReasonCode } from '../../api/consent'
import { useProviderAuth } from './ProviderAuthContext'

const MIN_JUSTIFICATION_LENGTH = 20
const OTHER_JUSTIFICATION_LENGTH = 80
const MAX_JUSTIFICATION_LENGTH = 500
const REASON_OPTIONS = BREAK_GLASS_REASON_OPTIONS

function maskToken(value: string): string {
  return value.length <= 12 ? '***' : `${value.slice(0, 6)}...${value.slice(-4)}`
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

export function EmergencyAccessScreen() {
  const router = useRouter()
  const { isAuthenticated, setAccessGrant } = useProviderAuth()
  const [patientId, setPatientId] = useState('')
  const [reasonCode, setReasonCode] = useState<BreakGlassReasonCode>('LIFE_THREATENING_EMERGENCY')
  const [justification, setJustification] = useState('')
  const [mfaCode, setMfaCode] = useState('')
  const [authorizationRef, setAuthorizationRef] = useState<string | null>(null)
  const [needsStepUp, setNeedsStepUp] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isAuthenticated) {
    return (
      <YStack
        flex={1}
        justify="center"
        items="center"
        gap="$4"
      >
        <Text>Provider session required.</Text>
        <Button onPress={() => router.push('/doctor/login')}>Go to login</Button>
      </YStack>
    )
  }

  const issueEmergencyAccess = async () => {
    const cleanPatientId = patientId.trim()
    const cleanJustification = justification.trim()
    if (!cleanPatientId) {
      setError('Patient ID is required.')
      return
    }
    const validationError = validateJustification(reasonCode, cleanJustification)
    if (validationError) {
      setError(validationError)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const result = await NexaApiClient.breakGlassIssue({
        patient_id: cleanPatientId,
        reason_code: reasonCode,
        justification: cleanJustification,
      })
      setAccessGrant({
        requestId: result.authorization_ref,
        patientId: cleanPatientId,
        consentToken: result.consent_token,
        purpose: 'EMERGENCY',
        scope: 'clinical',
        expiresAt: result.expires_at,
      })
      setAuthorizationRef(result.authorization_ref)
      setJustification('')
      setMfaCode('')
      router.push(`/doctor/patient-record?patient_id=${encodeURIComponent(cleanPatientId)}`)
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 428) {
        setNeedsStepUp(true)
        setError('Recent MFA verification is required for emergency access.')
      } else if (caught instanceof ApiError && caught.status === 409) {
        setError('A matching emergency request was just issued. Wait before retrying.')
      } else if (caught instanceof ApiError && caught.status === 403) {
        setError('Your current role is not authorized for emergency access.')
      } else {
        setError(
          'Emergency access could not be issued. Contact the clinical administrator if the emergency continues.'
        )
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
      setError('MFA verification failed or expired. Try a new code.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ScrollView>
      <YStack
        p="$5"
        gap="$5"
        maxW={600}
        mx="auto"
      >
        <XStack
          gap="$2"
          items="center"
        >
          <AlertTriangle
            size={36}
            color="$red10"
          />
          <Text
            fontSize={26}
            fontWeight="900"
            color="$red10"
          >
            Emergency access
          </Text>
        </XStack>
        <Card
          bg="$red2"
          borderWidth={1}
          borderColor="$red8"
          p="$4"
        >
          <Paragraph color="$red10">
            Limited, 15-minute access. This access is permanently recorded in the audit ledger, rate
            limited to 3 per hour, and may trigger patient notification and compliance review.
          </Paragraph>
        </Card>
        {authorizationRef && (
          <Card
            bg="$green2"
            borderWidth={1}
            borderColor="$green8"
            p="$4"
          >
            <Paragraph color="$green10">
              Authorization reference: {maskToken(authorizationRef)}
            </Paragraph>
          </Card>
        )}
        <Input
          value={patientId}
          onChangeText={setPatientId}
          placeholder="Canonical patient UUID"
        />
        <Select
          value={reasonCode}
          onValueChange={(value) => setReasonCode(value as BreakGlassReasonCode)}
        >
          <Select.Trigger iconAfter={ChevronDown}>
            <Select.Value />
          </Select.Trigger>
          <Select.Content zIndex={200000}>
            <Select.Viewport
              unstyled
              minWidth={280}
              maxHeight={320}
              backgroundColor="$background"
              borderWidth={1}
              borderColor="$borderColor"
              borderRadius="$4"
              padding="$1"
            >
              <Select.Group>
                {REASON_OPTIONS.map((option, index) => (
                  <Select.Item
                    key={option.value}
                    index={index}
                    value={option.value}
                  >
                    <Select.ItemText>{option.label}</Select.ItemText>
                  </Select.Item>
                ))}
              </Select.Group>
            </Select.Viewport>
          </Select.Content>
        </Select>
        <Input
          value={justification}
          onChangeText={setJustification}
          placeholder="Clinical justification"
          multiline
          maxLength={MAX_JUSTIFICATION_LENGTH}
        />
        {needsStepUp && (
          <Card
            p="$4"
            gap="$3"
          >
            <Text fontWeight="700">Step-up MFA required</Text>
            <Input
              value={mfaCode}
              onChangeText={setMfaCode}
              keyboardType="numeric"
              maxLength={6}
              secureTextEntry
            />
            <Button
              onPress={verifyStepUp}
              disabled={submitting}
            >
              Verify MFA and continue
            </Button>
          </Card>
        )}
        {error && <Text color="$red10">{error}</Text>}
        {!needsStepUp && (
          <Button
            theme="red"
            onPress={issueEmergencyAccess}
            disabled={submitting || !justification.trim()}
          >
            {submitting ? <Spinner /> : 'Issue minimum-necessary emergency access'}
          </Button>
        )}
        <Button
          chromeless
          onPress={() => router.push('/doctor/dashboard')}
        >
          Cancel
        </Button>
      </YStack>
    </ScrollView>
  )
}
