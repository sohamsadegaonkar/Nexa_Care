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

import {
  requestBreakGlassConsent,
  BreakGlassConsentError,
} from '../../api/consent'

const REASON_OPTIONS = [
  { label: 'Unconscious', value: 'UNCONSCIOUS' },
  { label: 'Cardiac Arrest', value: 'CARDIAC_ARREST' },
  { label: 'Severe Trauma', value: 'SEVERE_TRAUMA' },
  { label: 'Other Emergency', value: 'OTHER' },
]

interface BreakGlassScreenProps {
  onConsentIssued?: (patientId: string, token: string) => void
}

export function BreakGlassScreen({ onConsentIssued }: BreakGlassScreenProps) {
  const [patientId, setPatientId] = useState('')
  const [reasonCode, setReasonCode] = useState(REASON_OPTIONS[0].value)
  const [freeText, setFreeText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const handleBreakGlass = async () => {
    if (!patientId.trim() || !freeText.trim()) {
      setError('Patient ID and justification are required')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const token = await requestBreakGlassConsent(
        patientId.trim(),
        reasonCode,
        freeText.trim()
      )
      setSuccess(true)
      onConsentIssued?.(patientId.trim(), token)
    } catch (err: any) {
      if (err instanceof BreakGlassConsentError) {
        setError(err.message)
      } else {
        setError('Failed to issue emergency consent')
      }
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
              {REASON_OPTIONS.map((opt) => (
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

        <Button
          theme="red"
          size="$5"
          icon={AlertTriangle}
          disabled={loading || !patientId.trim() || !freeText.trim()}
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
