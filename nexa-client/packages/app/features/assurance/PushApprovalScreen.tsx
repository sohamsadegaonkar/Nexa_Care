'use client'

import { Button, Card, Text, XStack, YStack, Spinner } from '@my/ui'
import { useEffect, useState } from 'react'
import { issueRoutineConsentV1 } from '../../api/consent_v1'
import { requestPushApproval } from '../../api/assurance'

interface PushApprovalScreenProps {
  patientUuid: string
  clinicianName: string
  hospitalName: string
  purpose: string
  clinicianId: string
  hospitalId: string
  onApprove: () => void
  onDeny: () => void
  onTimeout: (fallbackToken?: string) => void
}

const TIMEOUT_SECONDS = 90

export function PushApprovalScreen({
  patientUuid,
  clinicianName,
  hospitalName,
  purpose,
  clinicianId,
  hospitalId,
  onApprove,
  onDeny,
  onTimeout,
}: PushApprovalScreenProps) {
  const [secondsLeft, setSecondsLeft] = useState(TIMEOUT_SECONDS)
  const [status, setStatus] = useState<'waiting' | 'approved' | 'denied' | 'timeout'>('waiting')
  const [backendError, setBackendError] = useState<string | null>(null)
  const [isInitiating, setIsInitiating] = useState(true)

  // Call backend when push request is initiated
  useEffect(() => {
    const initiatePush = async () => {
      setIsInitiating(true)
      try {
        const result = await requestPushApproval({
          patient_uuid: patientUuid,
          clinician_name: clinicianName,
          hospital_name: hospitalName,
          purpose,
        })
        
        if (result.approved) {
          setStatus('approved')
          onApprove()
        }
      } catch (e) {
        setBackendError('Failed to send push notification')
        console.error('Failed to initiate push approval', e)
      } finally {
        setIsInitiating(false)
      }
    }
    void initiatePush()
  }, [patientUuid, clinicianName, hospitalName, purpose])

  useEffect(() => {
    const interval = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          clearInterval(interval)
          setStatus('timeout')
          void recordStandardFallback()
          return 0
        }
        return s - 1
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [])

  const recordStandardFallback = async () => {
    try {
      // Notify backend that push timed out
      await requestPushApproval({
        patient_uuid: patientUuid,
        clinician_name: clinicianName,
        hospital_name: hospitalName,
        purpose,
      })

      const response = await issueRoutineConsentV1({
        patient_uuid: patientUuid,
        hospital_id: hospitalId,
        clinician_id: clinicianId,
        purpose,
        consent_assurance: 'standard_fallback_from_push',
      })
      onTimeout(response.consent_token)
    } catch {
      onTimeout()
    }
  }

  const handleApprove = () => {
    setStatus('approved')
    onApprove()
  }

  const handleDeny = () => {
    setStatus('denied')
    onDeny()
  }

  if (status === 'timeout') {
    return (
      <YStack flex={1} items="center" justify="center" bg="$background" p="$6" gap="$4">
        <Text fontSize={22} fontWeight="900" color="$red11">Request Expired</Text>
        <Text color="$color11" text="center">No response in 90 seconds. Standard fallback recorded.</Text>
        {backendError && <Text color="$red11">{backendError}</Text>}
        <Button theme="blue" onPress={() => window.location.reload()}>Return to Home</Button>
      </YStack>
    )
  }

  return (
    <YStack flex={1} bg="$background" p="$6" gap="$6" justify="center">
      <YStack gap="$3" items="center">
        <Text fontSize={20} fontWeight="700" color="$color12">Access Request</Text>
        <Text color="$color11" text="center">
          Dr. {clinicianName} at {hospitalName} wants to access your record for:
        </Text>
        <Text fontSize={18} fontWeight="800" color="$blue11">{purpose}</Text>
      </YStack>

      <Card p="$5" bg="$color2" borderWidth={2} borderColor="$yellow8">
        <YStack gap="$2" items="center">
          <Text color="$color11">Time remaining</Text>
          <Text fontSize={48} fontWeight="900" color="$color12">{secondsLeft}s</Text>
        </YStack>
      </Card>

      <XStack gap="$3" justify="center">
        <Button 
          theme="red" 
          size="$5" 
          flex={1} 
          onPress={handleDeny} 
          disabled={status !== 'waiting' || isInitiating}
        >
          Deny
        </Button>
        <Button 
          theme="green" 
          size="$5" 
          flex={1} 
          onPress={handleApprove} 
          disabled={status !== 'waiting' || isInitiating}
        >
          {isInitiating ? 'Sending Request...' : 'Approve'}
        </Button>
      </XStack>

      {backendError && (
        <Text color="$red11" fontSize={14} text="center">
          {backendError}
        </Text>
      )}

      <Text fontSize={13} color="$color10" text="center">
        If you do nothing, the request will fall back to Standard after 90 seconds.
      </Text>
    </YStack>
  )
}
