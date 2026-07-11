'use client'

import { Button, Card, H1, H4, Paragraph, Spinner, Text, YStack, XStack, Theme } from '@my/ui'
import { CheckCircle, XCircle, ShieldAlert } from '@tamagui/lucide-icons'
import { useEffect, useState, useCallback } from 'react'
import { getPushRequestStatus, respondToPushRequest, type PushApprovalStatusResponse } from '../../api/assurance'
import { signConsentChallenge, getDeviceId } from '../../utils/deviceKey'

interface PatientApprovalScreenProps {
  requestId: string
}

type ScreenState = 
  | 'loading' 
  | 'approval' 
  | 'biometric' 
  | 'submitting' 
  | 'success_approved' 
  | 'success_denied' 
  | 'expired' 
  | 'error'

const TIMEOUT_WINDOW_SECONDS = 90

export function PatientApprovalScreen({ requestId }: PatientApprovalScreenProps) {
  const [state, setState] = useState<ScreenState>('loading')
  const [request, setRequest] = useState<PushApprovalStatusResponse | null>(null)
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getPushRequestStatus(requestId)
      setRequest(data)
      
      if (data.status !== 'pending') {
        setState(data.status === 'approved' ? 'success_approved' : data.status === 'denied' ? 'success_denied' : 'expired')
        return
      }

      // Calculate remaining time
      const created = new Date(data.created_at).getTime()
      const now = new Date().getTime()
      const diff = Math.floor((now - created) / 1000)
      const remaining = TIMEOUT_WINDOW_SECONDS - diff

      if (remaining <= 0) {
        setState('expired')
      } else {
        setSecondsLeft(remaining)
        setState('approval')
      }
    } catch (err) {
      setErrorMessage('Failed to load request details.')
      setState('error')
    }
  }, [requestId])

  useEffect(() => {
    void fetchStatus()
  }, [fetchStatus])

  useEffect(() => {
    if (state !== 'approval' || secondsLeft === null) return

    const interval = setInterval(() => {
      setSecondsLeft((prev) => {
        if (prev === null || prev <= 1) {
          clearInterval(interval)
          setState('expired')
          return 0
        }
        return prev - 1
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [state, secondsLeft])

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const getTimerColor = (seconds: number) => {
    if (seconds > 60) return '$green10'
    if (seconds > 30) return '$yellow10'
    return '$red10'
  }

  const handleApprove = async () => {
    if (!request) return

    setState('biometric')

    try {
      // signConsentChallenge() uses the canonical 9-pipe signing input
      // and internally prompts Face ID/Touch ID/fingerprint via
      // expo-local-authentication before accessing the private key.
      const signature = await signConsentChallenge({
        request_id: requestId,
        patient_id: request.patient_id,
        provider_id: request.clinician_id,
        challenge_nonce: request.nonce,
        decision: 'approved',
        scope: request.scope,
        purpose: request.purpose,
        access_duration: request.access_duration,
        expires_at: request.expires_at,
      })

      setState('submitting')

      const deviceId = await getDeviceId()
      await respondToPushRequest(requestId, {
        decision: 'approved',
        signature,
        nonce: request.nonce,
        device_id: deviceId ?? undefined,
      })
      setState('success_approved')
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Authentication failed.')
      setState('error')
    }
  }

  const handleDeny = async () => {
    setState('submitting')
    try {
      await respondToPushRequest(requestId, { decision: 'denied' })
      setState('success_denied')
    } catch (err) {
      setErrorMessage('Failed to submit denial.')
      setState('error')
    }
  }

  if (state === 'loading') {
    return (
      <YStack flex={1} items="center" justify="center" p="$6">
        <Spinner size="large" color="$blue10" />
        <Paragraph mt="$4">Verifying request...</Paragraph>
      </YStack>
    )
  }

  if (state === 'error') {
    return (
      <YStack flex={1} items="center" justify="center" p="$6" gap="$4">
        <ShieldAlert size={64} color="$red10" />
        <H4 text="center">Operation Failed</H4>
        <Paragraph text="center" color="$red11">{errorMessage}</Paragraph>
        <Button mt="$4" theme="blue" onPress={() => void fetchStatus()}>Retry</Button>
      </YStack>
    )
  }

  if (state === 'expired') {
    return (
      <YStack flex={1} items="center" justify="center" p="$6" gap="$4">
        <XCircle size={64} color="$gray10" />
        <H4 text="center">Request Expired</H4>
        <Paragraph text="center">This request is no longer active. No action is required.</Paragraph>
        <Button mt="$4" theme="blue" onPress={() => window.location.reload()}>Close</Button>
      </YStack>
    )
  }

  if (state === 'success_approved') {
    return (
      <YStack flex={1} items="center" justify="center" p="$6" gap="$4">
        <CheckCircle size={64} color="$green10" />
        <H1 color="$green10">Approved</H1>
        <Paragraph text="center">
          Access granted to Dr. {request?.clinician_name}.
        </Paragraph>
      </YStack>
    )
  }

  if (state === 'success_denied') {
    return (
      <YStack flex={1} items="center" justify="center" p="$6" gap="$4">
        <XCircle size={64} color="$red10" />
        <H1 color="$red10">Denied</H1>
        <Paragraph text="center">
          Access denied. Dr. {request?.clinician_name} has been notified.
        </Paragraph>
      </YStack>
    )
  }

  if (state === 'biometric' || state === 'submitting') {
    return (
      <YStack flex={1} items="center" justify="center" p="$6">
        <Spinner size="large" color="$blue10" />
        <Paragraph mt="$4">
          {state === 'biometric' ? 'Awaiting biometric confirmation...' : 'Sending your decision...'}
        </Paragraph>
      </YStack>
    )
  }

  return (
    <YStack flex={1} items="center" justify="center" p="$6" gap="$6" bg="$background">
      <YStack gap="$2" items="center">
        <H4>Security Consent</H4>
        <Paragraph text="center" color="$color11">
          A clinician is requesting temporary access to your medical records.
        </Paragraph>
      </YStack>

      <Card width="100%" maxW={400} p="$5" bg="$background" borderRadius="$6" elevation="$2" gap="$4">
        <YStack gap="$1">
          <Text fontSize={13} color="$color10" fontWeight="700" textTransform="uppercase">Clinician</Text>
          <Text fontSize={18} fontWeight="800">Dr. {request?.clinician_name}</Text>
          <Text fontSize={14} color="$color11">{request?.hospital_name}</Text>
        </YStack>

        <YStack gap="$1">
          <Text fontSize={13} color="$color10" fontWeight="700" textTransform="uppercase">Purpose</Text>
          <Text fontSize={16}>{request?.purpose}</Text>
        </YStack>

        <YStack items="center" py="$2" borderTopWidth={1} borderColor="$color3">
          <Text fontSize={12} color="$color10">Time Remaining</Text>
          <H1 color={getTimerColor(secondsLeft || 0)}>
            {formatTime(secondsLeft || 0)}
          </H1>
        </YStack>
      </Card>

      <YStack width="100%" maxW={400} gap="$3">
        <Button 
          backgroundColor="$green9" 
          size="$6" 
          width="100%" 
          onPress={handleApprove}
          color="white"
          fontWeight="800"
        >
          Approve
        </Button>
        <Button 
          backgroundColor="$red9" 
          size="$6" 
          width="100%" 
          onPress={handleDeny}
          color="white"
          fontWeight="800"
        >
          Deny
        </Button>
      </YStack>

      <Paragraph text="center" fontSize={12} color="$color10">
        Approving will require biometric confirmation to prove your identity.
      </Paragraph>
    </YStack>
  )
}