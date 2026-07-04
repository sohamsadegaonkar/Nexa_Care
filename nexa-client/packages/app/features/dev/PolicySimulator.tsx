'use client'

import { Button, Card, Text, XStack, YStack } from '@my/ui'
import { useState } from 'react'
import { ConsentAssurance } from '../../api/consent_v1'
import { updatePatientPolicy } from '../../api/policy'

interface PolicySimulatorProps {
  patientUuid: string
  onPolicyChange?: (policy: ConsentAssurance) => void
}

const POLICIES: ConsentAssurance[] = [
  'standard',
  'push_approved',
  'biometric_confirmed',
  'standard_fallback_from_push'
]

export function PolicySimulator({ patientUuid, onPolicyChange }: PolicySimulatorProps) {
  const [loading, setLoading] = useState(false)
  const [currentPolicy, setCurrentPolicy] = useState<ConsentAssurance>('standard')

  const setPolicy = async (policy: ConsentAssurance) => {
    setLoading(true)
    try {
      await updatePatientPolicy(patientUuid, policy, { 
        headers: { 'X-Dev-Simulator': 'true' } 
      })
      setCurrentPolicy(policy)
      onPolicyChange?.(policy)
    } catch (e: any) {
      const message = e?.response?.data?.detail || 'Failed to update policy'
      alert(`Error: ${message}`)
      console.error('Failed to set policy', e)
    } finally {
      setLoading(false)
    }
  }

  if (process.env.NODE_ENV === 'production') return null

  return (
    <Card p="$4" bg="$color2" borderWidth={1} borderColor="$borderColor" mt="$4">
      <YStack gap="$3">
        <Text fontSize={14} fontWeight="700" color="$color11">
          [DEV] Policy Simulator
        </Text>
        <Text fontSize={12} color="$color10">Patient: {patientUuid}</Text>

        <XStack gap="$2" flexWrap="wrap">
          {POLICIES.map((policy) => (
            <Button
              key={policy}
              size="$3"
              theme={currentPolicy === policy ? 'blue' : undefined}
              disabled={loading}
              onPress={() => setPolicy(policy)}
            >
              {policy}
            </Button>
          ))}
        </XStack>
      </YStack>
    </Card>
  )
}
