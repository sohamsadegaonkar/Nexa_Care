import { useRouter, useLocalSearchParams } from 'expo-router'
import { YStack, H2, Paragraph, Button, Spinner, Text } from 'tamagui'
import { useState, useEffect } from 'react'
import {
  fetchChallenge,
  approveWithBiometric,
  isChallengeExpired,
  type ConsentChallenge,
} from '../../services/consentSigning'

/**
 * Biometric approval screen — Face ID / Touch ID → sign → submit.
 *
 * ALPHA: P-256 keypair generated client-side and private key stored in
 * platform secure storage. Not yet: hardware-backed non-exportable
 * signing key with biometric-gated key usage.
 *
 * The signing input matches signed_approval_verifier.py byte-for-byte:
 *   request_id|patient_id|provider_id|challenge_nonce|decision|
 *   scope|purpose|access_duration|expires_at
 *
 * The private key is ONLY accessed after successful biometric authentication.
 */

export interface ConsentReceipt {
  grantId: string
  providerName: string
  scope: string[]
  expiresAt: string
}

interface BiometricApprovalScreenProps {
  requestId?: string
  onApproved?: (receipt: ConsentReceipt) => void
  onCancelled?: () => void
}

export default function BiometricApprovalScreen({
  requestId: requestIdProp,
  onApproved,
  onCancelled,
}: BiometricApprovalScreenProps) {
  const router = useRouter()
  const params = useLocalSearchParams<{ requestId?: string }>()
  const requestId = requestIdProp ?? params.requestId ?? ''

  const [challenge, setChallenge] = useState<ConsentChallenge | null>(null)
  const [status, setStatus] = useState<'loading' | 'prompt' | 'authenticating' | 'signing' | 'submitting' | 'error' | 'expired'>('loading')
  const [error, setError] = useState<string | null>(null)

  // Fetch challenge on mount
  useEffect(() => {
    if (!requestId) {
      setStatus('error')
      setError('No request ID provided.')
      return
    }
    let cancelled = false

    async function load() {
      try {
        const data = await fetchChallenge(requestId)
        if (cancelled) return
        if (isChallengeExpired(data) || data.status !== 'pending') {
          setChallenge(data)
          setStatus('expired')
          return
        }
        setChallenge(data)
        setStatus('prompt')
      } catch {
        if (!cancelled) {
          setStatus('expired')
          setError('This request has expired or is not available.')
        }
      }
    }

    load()
    return () => { cancelled = true }
  }, [requestId])

  const handleBiometricAuth = async () => {
    if (!challenge) return
    setStatus('authenticating')
    setError(null)

    try {
      // approveWithBiometric gates with biometric → signs → submits
      setStatus('authenticating')
      const result = await approveWithBiometric(challenge)

      // Navigate to result screen on success
      const receipt: ConsentReceipt = {
        grantId: result.request_id,
        providerName: challenge.provider_name,
        scope: typeof challenge.scope === 'string'
          ? challenge.scope.split(',').map((s) => s.trim())
          : challenge.scope,
        expiresAt: challenge.expires_at,
      }

      onApproved?.(receipt)
      router.replace({
        pathname: '/patient/approval-result',
        params: {
          requestId: challenge.request_id,
          decision: 'approved',
          providerName: challenge.provider_name,
          scope: receipt.scope.join(','),
          expiresAt: challenge.expires_at,
        },
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Approval failed'
      if (message.includes('cancelled') || message.includes('cancel')) {
        // User cancelled biometric — go back to prompt
        setStatus('prompt')
        setError('Biometric verification cancelled.')
      } else {
        setStatus('error')
        setError(message)
      }
    }
  }

  const handleCancel = () => {
    onCancelled?.()
    router.back()
  }

  // ── Render: Loading ──────────────────────────────────────────────────
  if (status === 'loading') {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center">
        <Spinner size="large" color="$blue10" />
        <Paragraph col="$colorSubdued" size="$4" mt="$3">Loading request...</Paragraph>
      </YStack>
    )
  }

  // ── Render: Expired ──────────────────────────────────────────────────
  if (status === 'expired') {
    return (
      <YStack f={1} bg="$background" p="$4" gap="$4" jc="center" ai="center">
        <Text fontSize={56}>⏰</Text>
        <H2 col="$color" ta="center">Request Expired</H2>
        <Paragraph col="$colorSubdued" ta="center" size="$4">
          {error ?? 'This consent request has expired. No action is needed.'}
        </Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.replace('/patient/access-history')}>
          Go to Access History
        </Button>
      </YStack>
    )
  }

  // ── Render: Prompt ───────────────────────────────────────────────────
  if (status === 'prompt') {
    return (
      <YStack f={1} bg="$background" p="$4" gap="$4" jc="center" ai="center">
        <Text fontSize={56}>👆</Text>
        <H2 col="$color" ta="center">Verify Your Approval</H2>
        <Paragraph col="$colorSubdued" ta="center" size="$4" mw={320}>
          Use Face ID or your fingerprint to confirm that you approve
          this data access request.
        </Paragraph>
        <Paragraph col="$orange10" ta="center" size="$2" mw={320}>
          ALPHA: P-256 keypair generated client-side and private key stored
          in platform secure storage. Not yet: hardware-backed non-exportable
          signing key with biometric-gated key usage.
        </Paragraph>
        {error && (
          <Text col="$red10" ta="center" size="$3">{error}</Text>
        )}
        <YStack gap="$3" w="100%" mt="$4">
          <Button theme="blue" size="$4" onPress={handleBiometricAuth}>
            Authenticate
          </Button>
          <Button size="$4" chromeless onPress={handleCancel}>
            Cancel
          </Button>
        </YStack>
      </YStack>
    )
  }

  // ── Render: Authenticating / Signing / Submitting ────────────────────
  if (status === 'authenticating' || status === 'signing' || status === 'submitting') {
    const labels: Record<string, { title: string; subtitle: string }> = {
      authenticating: {
        title: 'Verifying identity...',
        subtitle: 'Hold still for Face ID verification',
      },
      signing: {
        title: 'Signing consent...',
        subtitle: 'Encrypting your approval with device key',
      },
      submitting: {
        title: 'Submitting approval...',
        subtitle: 'Sending signed approval to Nexa Care',
      },
    }
    const label = labels[status]

    return (
      <YStack f={1} bg="$background" p="$4" gap="$3" jc="center" ai="center">
        <Spinner size="large" color="$blue10" />
        <H2 col="$color" ta="center">{label.title}</H2>
        <Paragraph col="$colorSubdued" ta="center" size="$3">{label.subtitle}</Paragraph>
      </YStack>
    )
  }

  // ── Render: Error ────────────────────────────────────────────────────
  return (
    <YStack f={1} bg="$background" p="$4" gap="$3" jc="center" ai="center">
      <Text fontSize={56}>❌</Text>
      <H2 col="$color" ta="center">Approval Failed</H2>
      <Paragraph col="$colorSubdued" ta="center" size="$4">
        {error ?? 'Something went wrong. Please try again.'}
      </Paragraph>
      <Button theme="blue" size="$4" onPress={handleBiometricAuth}>
        Try Again
      </Button>
      <Button size="$4" chromeless onPress={handleCancel}>
        Go Back
      </Button>
    </YStack>
  )
}
