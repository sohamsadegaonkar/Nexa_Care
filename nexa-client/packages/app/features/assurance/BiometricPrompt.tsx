'use client'

import { Button, Card, Text, YStack, XStack, Spinner } from '@my/ui'
import { Fingerprint } from '@tamagui/lucide-icons'
import { useState } from 'react'
import { Platform } from 'react-native'
import { verifyBiometric } from '../../api/assurance'

interface BiometricPromptProps {
  patientUuid: string
  onSuccess: () => void
  onCancel: () => void
}

export function BiometricPrompt({ onSuccess, onCancel }: BiometricPromptProps) {
  const [verifying, setVerifying] = useState(false)
  const isNative = Platform.OS !== 'web'

  const [error, setError] = useState<string | null>(null)

  const handleVerify = async () => {
    setVerifying(true)
    setError(null)
    try {
      const result = await verifyBiometric({
        patient_uuid: patientUuid,
        biometric_token: 'mobile-biometric-assertion',
      })
      if (result.verified) {
        onSuccess()
      } else {
        setError('Verification failed')
        onCancel()
      }
    } catch (e) {
      console.error('Biometric verification failed', e)
      setError('Verification failed. Please try again.')
      onCancel()
    } finally {
      setVerifying(false)
    }
  }

  return (
    <YStack flex={1} bg="$background" items="center" justify="center" p="$6" gap="$6">
      <Card p="$6" bg="$color2" borderWidth={2} borderColor="$blue8" width="100%" maxW={340}>
        <YStack gap="$4" items="center">
          <Fingerprint size={64} color="$blue10" />
          <Text fontSize={22} fontWeight="900" color="$color12" text="center">
            Biometric Confirmation Required
          </Text>
          <Text color="$color11" text="center">
            Please authenticate with your fingerprint or Face ID
          </Text>

          {verifying ? (
            <XStack gap="$2" items="center">
              <Spinner color="$blue11" />
              <Text color="$color11">Verifying...</Text>
            </XStack>
          ) : (
            <Button theme="blue" size="$5" onPress={handleVerify} disabled={!isNative}>
              {isNative ? 'Authenticate' : 'Simulate Biometric (Web)'}
            </Button>
          )}

          {error && (
            <Text color="$red11" fontSize={14} text="center">
              {error}
            </Text>
          )}
        </YStack>
      </Card>

      <Button chromeless onPress={onCancel}>
        Cancel
      </Button>
    </YStack>
  )
}
