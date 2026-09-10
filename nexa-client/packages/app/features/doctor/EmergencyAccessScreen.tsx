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
      router.push('/doctor/patient-record')
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
        maxW={660}
        mx="auto"
      >
        <XStack
          gap="$3"
          items="center"
        >
          <YStack
            padding="$2.5"
            borderRadius={12}
            backgroundColor="$nexaDangerSoft"
          >
            <AlertTriangle
              size={36}
              color="$nexaDanger"
            />
          </YStack>
          <YStack gap="$0.5">
            <Text
              fontSize={26}
              fontWeight="900"
              color="$nexaDanger"
              letterSpacing={-0.6}
            >
              Emergency access
            </Text>
            <Text
              color="$nexaSecondary"
              fontSize={14}
            >
              Controlled Break-Glass Protocol
            </Text>
          </YStack>
        </XStack>

        <Card
          bg="$nexaDangerSoft"
          borderWidth={1}
          borderColor="$nexaDanger"
          borderRadius={14}
          p="$4.5"
          gap="$2"
        >
          <Text color="$nexaDanger" fontWeight="800" fontSize={15}>
            Strict Audit & Urgent Care Boundary
          </Text>
          <Paragraph color="$nexaDanger" fontSize={13} lineHeight={20}>
            Limited, 15-minute access. This access is permanently recorded in the audit ledger, rate
            limited to 3 per hour, and recorded in the patient's audit history for clinical review.
          </Paragraph>
        </Card>

        {authorizationRef && (
          <Card
            bg="$nexaSuccessSoft"
            borderWidth={1}
            borderColor="$nexaSuccess"
            borderRadius={14}
            p="$4"
          >
            <Paragraph color="$nexaSuccess" fontWeight="700">
              Authorization reference: {maskToken(authorizationRef)}
            </Paragraph>
          </Card>
        )}

        <YStack gap="$2">
          <Text color="$nexaText" fontSize={14} fontWeight="700">
            Patient Target Identifier
          </Text>
          <Input
            value={patientId}
            onChangeText={setPatientId}
            placeholder="Canonical patient UUID"
            minHeight={50}
            borderRadius={10}
            borderColor="$nexaBorder"
            backgroundColor="$nexaSurface"
          />
        </YStack>

        <YStack gap="$2">
          <Text color="$nexaText" fontSize={14} fontWeight="700">
            Approved Emergency Reason Code
          </Text>
          <Select
            value={reasonCode}
            onValueChange={(value) => setReasonCode(value as BreakGlassReasonCode)}
          >
            <Select.Trigger
              iconAfter={ChevronDown}
              minHeight={50}
              borderRadius={10}
              borderColor="$nexaBorder"
              backgroundColor="$nexaSurface"
            >
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
        </YStack>

        <YStack gap="$2">
          <XStack justifyContent="space-between" alignItems="center">
            <Text color="$nexaText" fontSize={14} fontWeight="700">
              Clinical Justification (Mandatory)
            </Text>
            <Text
              color={
                justification.trim().length >=
                (reasonCode === 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY' ? 80 : 20)
                  ? '$nexaSuccess'
                  : '$nexaWarning'
              }
              fontSize={12}
              fontWeight="700"
            >
              {justification.length}/{MAX_JUSTIFICATION_LENGTH} chars (min {reasonCode === 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY' ? 80 : 20})
            </Text>
          </XStack>
          <Input
            value={justification}
            onChangeText={setJustification}
            placeholder="Clinical justification"
            multiline
            numberOfLines={4}
            minHeight={100}
            borderRadius={10}
            borderColor="$nexaBorder"
            backgroundColor="$nexaSurface"
            maxLength={MAX_JUSTIFICATION_LENGTH}
          />
        </YStack>

        {needsStepUp && (
          <Card
            p="$4"
            gap="$3"
            borderRadius={12}
            borderWidth={1}
            borderColor="$nexaWarning"
            backgroundColor="$nexaWarningSoft"
          >
            <Text fontWeight="800" color="$nexaWarning">Step-up MFA required</Text>
            <Paragraph color="$nexaText" fontSize={13}>
              Enter the current 6-digit authenticator code to authorize emergency access.
            </Paragraph>
            <Input
              value={mfaCode}
              onChangeText={setMfaCode}
              placeholder="000000"
              keyboardType="numeric"
              maxLength={6}
              minHeight={48}
              secureTextEntry
              backgroundColor="$nexaSurface"
            />
            <Button
              theme="blue"
              onPress={verifyStepUp}
              disabled={submitting}
            >
              Verify MFA and continue
            </Button>
          </Card>
        )}

        {error && (
          <Card
            p="$3"
            borderRadius={8}
            backgroundColor="$nexaDangerSoft"
            borderLeftWidth={4}
            borderLeftColor="$nexaDanger"
          >
            <Text color="$nexaDanger" fontWeight="700">{error}</Text>
          </Card>
        )}

        {!needsStepUp && (
          <Button
            theme="red"
            size="$5"
            borderRadius={10}
            onPress={issueEmergencyAccess}
            disabled={submitting || !justification.trim()}
          >
            {submitting ? <Spinner color="$nexaOnAccent" /> : 'Issue minimum-necessary emergency access'}
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
