'use client'

import { Button, Card, Text, XStack, YStack } from '@my/ui'
import { useState } from 'react'
import { ConsentAssurance } from '../../api/consent_v1'

interface PolicyScreenProps {
  currentPolicy: ConsentAssurance
  patientUuid?: string
  onPolicyChange: (policy: ConsentAssurance) => Promise<void>
}

const POLICY_OPTIONS: { label: string; value: ConsentAssurance; desc: string }[] = [
  { label: 'Standard', value: 'standard', desc: 'NFC tap only — fastest' },
  { label: 'Push Approval', value: 'push_approved', desc: 'Real-time notification (90s)' },
  { label: 'Biometric', value: 'biometric_confirmed', desc: 'Fingerprint / Face ID required' },
]

export function PolicyScreen({ currentPolicy, patientUuid, onPolicyChange }: PolicyScreenProps) {
  const [selected, setSelected] = useState<ConsentAssurance>(currentPolicy)
  const [loading, setLoading] = useState(false)

  const handleSave = async () => {
    setLoading(true)
    try {
      await onPolicyChange(selected)
      
      // Persist to backend if patientUuid is provided
      if (patientUuid) {
        const { updatePatientPolicy } = await import('../../api/policy')
        await updatePatientPolicy(patientUuid, selected)
      }
      
      alert(`Policy saved: ${selected}`)
    } catch (e) {
      alert("Failed to save policy")
    } finally {
      setLoading(false)
    }
  }

  return (
    <YStack flex={1} bg="$background" p="$5" gap="$6">
      <YStack gap="$2">
        <Text fontSize={26} fontWeight="900" color="$color12">
          Consent Assurance Policy
        </Text>
        <Text color="$color11">You control how your records are accessed</Text>
      </YStack>

      <YStack gap="$3">
        {POLICY_OPTIONS.map((option) => (
          <Card
            key={option.value}
            p="$4"
            borderWidth={selected === option.value ? 3 : 1}
            borderColor={selected === option.value ? '$blue8' : '$borderColor'}
            bg="$color2"
            pressStyle={{ scale: 0.985 }}
            onPress={() => setSelected(option.value)}
          >
            <YStack gap="$1">
              <Text fontSize={18} fontWeight="800" color="$color12">
                {option.label}
              </Text>
              <Text color="$color11" fontSize={15}>
                {option.desc}
              </Text>
            </YStack>
          </Card>
        ))}
      </YStack>

      <Button
        theme="blue"
        size="$5"
        disabled={loading || selected === currentPolicy}
        onPress={handleSave}
      >
        {loading ? 'Saving...' : 'Save Policy'}
      </Button>

      <Text fontSize={13} color="$color10" text="center">
        Standard is always available. Upgrades require your approval.
      </Text>
    </YStack>
  )
}
