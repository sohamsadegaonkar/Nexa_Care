'use client'

import {
  Button,
  Card,
  Input,
  Text,
  TextArea,
  YStack,
  XStack,
  Spinner,
} from '@my/ui'
import { AlertTriangle } from '@tamagui/lucide-icons'
import { useState } from 'react'
import { NexaApiClient } from '../../utils/apiClient'

import {
  requestBreakGlassConsent,
  BreakGlassConsentError,
  BREAK_GLASS_REASON_OPTIONS,
  type BreakGlassReasonCode,
} from '../../api/consent'

interface BreakGlassScreenProps {
  onConsentIssued?: (patientId: string, token: string) => void
}

export function BreakGlassScreen({ onConsentIssued }: BreakGlassScreenProps) {
  const [patientId, setPatientId] = useState('')
  const [reasonCode, setReasonCode] = useState<BreakGlassReasonCode>('UNCONSCIOUS_PATIENT')
  const [freeText, setFreeText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [needsStepUp, setNeedsStepUp] = useState(false)
  const [mfaCode, setMfaCode] = useState('')

  const handleBreakGlass = async () => {
    if (!patientId.trim() || !freeText.trim()) {
      setError('Patient ID and justification are required')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const result = await requestBreakGlassConsent(
        patientId.trim(),
        reasonCode,
        freeText.trim()
      )
      setSuccess(true)
      onConsentIssued?.(patientId.trim(), result.consent_token)
    } catch (err: any) {
      if (err instanceof BreakGlassConsentError) {
        if (err.status === 428) {
          setNeedsStepUp(true)
          setError('Recent MFA verification is required.')
        } else setError(err.message)
      } else {
        setError('Failed to issue emergency consent')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleStepUp = async () => {
    if (!/^\d{6}$/.test(mfaCode)) {
      setError('Enter a valid 6-digit authenticator code.')
      return
    }
    setLoading(true)
    try {
      await NexaApiClient.verifyActionMfa(mfaCode)
      setNeedsStepUp(false)
      setMfaCode('')
      await handleBreakGlass()
    } catch {
      setError('MFA verification failed. Use a new code and try again.')
    } finally {
      setLoading(false)
    }
  }

  if (success) {
    return (
      <YStack
        flex={1}
        bg="$background"
        items="center"
        justify="center"
        p="$6"
        gap="$4"
      >
        <AlertTriangle size={64} color="$orange11" />
        <Text fontSize={24} fontWeight="900" color="$color12" text="center">
          Emergency Access Granted
        </Text>
        <Text color="$color11" text="center" maxW={320}>
          Break-glass consent token issued. This action has been audited.
        </Text>
        <Button theme="blue" onPress={() => window.location.reload()}>
          Return to Home
        </Button>
      </YStack>
    )
  }

  return (
    <YStack flex={1} bg="$background" p="$5" gap="$5">
      <YStack gap="$2" items="center">
        <AlertTriangle size={48} color="$red11" />
        <Text fontSize={26} fontWeight="900" color="$color12">
          Emergency Break-Glass
        </Text>
        <Text color="$red11" text="center" fontSize={15}>
          This action is fully audited and should only be used in true emergencies.
        </Text>
      </YStack>

      <Card p="$5" bg="$color2" borderWidth={2} borderColor="$red8" gap="$5">
        <YStack gap="$4">
          <YStack gap="$2">
            <Text color="$color11" fontWeight="700">Patient ID</Text>
            <Input
              placeholder="PAT-XXXX-XXXX"
              value={patientId}
              onChangeText={setPatientId}
              size="$5"
              autoCapitalize="characters"
            />
          </YStack>

          <YStack gap="$2">
            <Text color="$color11" fontWeight="700">Reason Code</Text>
            <XStack gap="$2" flexWrap="wrap">
              {BREAK_GLASS_REASON_OPTIONS.map((opt) => (
                <Button
                  key={opt.value}
                  size="$3"
                  theme={reasonCode === opt.value ? 'red' : undefined}
                  onPress={() => setReasonCode(opt.value)}
                >
                  {opt.label}
                </Button>
              ))}
            </XStack>
          </YStack>

          <YStack gap="$2">
            <Text color="$color11" fontWeight="700">Justification (required)</Text>
            <TextArea
              placeholder="Describe the emergency situation..."
              value={freeText}
              onChangeText={setFreeText}
              minHeight={120}
            />
          </YStack>
        </YStack>

        {error && (
          <Text color="$red11" fontSize={14}>
            {error}
          </Text>
        )}

        {needsStepUp && (
          <YStack gap="$3">
            <Input
              placeholder="6-digit authenticator code"
              value={mfaCode}
              onChangeText={setMfaCode}
              keyboardType="numeric"
              maxLength={6}
              secureTextEntry
            />
            <Button onPress={handleStepUp} disabled={loading}>Verify MFA and continue</Button>
          </YStack>
        )}

        <Button
          theme="red"
          size="$5"
          icon={AlertTriangle}
          disabled={needsStepUp || loading || !patientId.trim() || !freeText.trim()}
          onPress={handleBreakGlass}
        >
          {loading ? (
            <XStack gap="$2" items="center">
              <Spinner color="$color12" /> Issuing Emergency Token...
            </XStack>
          ) : (
            'ISSUE EMERGENCY ACCESS'
          )}
        </Button>
      </Card>
    </YStack>
  )
}