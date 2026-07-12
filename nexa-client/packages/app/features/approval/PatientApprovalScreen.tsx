'use client'

import { Button, H4, Paragraph, Spinner, YStack } from '@my/ui'
import { useEffect, useState } from 'react'
import {
  approveWithBiometric,
  denyWithSignature,
  fetchChallenge,
  isChallengeExpired,
  type ConsentChallenge,
} from '../../services/consentSigning'

interface PatientApprovalScreenProps { requestId: string }
type State = 'loading' | 'ready' | 'submitting' | 'approved' | 'denied' | 'expired' | 'error'

/** Next-compatible canonical signed-approval screen for existing push deep links. */
export function PatientApprovalScreen({ requestId }: PatientApprovalScreenProps) {
  const [challenge, setChallenge] = useState<ConsentChallenge | null>(null)
  const [state, setState] = useState<State>('loading')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetchChallenge(requestId).then((data) => {
      if (cancelled) return
      setChallenge(data)
      setState(isChallengeExpired(data) || data.status !== 'pending' ? 'expired' : 'ready')
    }).catch(() => {
      if (!cancelled) { setError('This request has expired or is unavailable.'); setState('error') }
    })
    return () => { cancelled = true }
  }, [requestId])

  const approve = async () => {
    if (!challenge) return
    setState('submitting')
    try { await approveWithBiometric(challenge); setState('approved') }
    catch (err) { setError(err instanceof Error ? err.message : 'Approval failed.'); setState('error') }
  }

  const deny = async () => {
    if (!challenge) return
    setState('submitting')
    try { await denyWithSignature(challenge); setState('denied') }
    catch (err) { setError(err instanceof Error ? err.message : 'Denial failed.'); setState('error') }
  }

  if (state === 'loading' || state === 'submitting') return <YStack flex={1} items="center" justify="center"><Spinner /><Paragraph>{state === 'loading' ? 'Loading request...' : 'Submitting signed decision...'}</Paragraph></YStack>
  if (state === 'approved') return <YStack flex={1} items="center" justify="center"><H4>Approved</H4><Paragraph>Signed consent approval verified.</Paragraph></YStack>
  if (state === 'denied') return <YStack flex={1} items="center" justify="center"><H4>Denied</H4><Paragraph>Signed denial recorded.</Paragraph></YStack>
  if (state === 'expired') return <YStack flex={1} items="center" justify="center"><H4>Request Expired</H4></YStack>
  if (state === 'error') return <YStack flex={1} items="center" justify="center"><H4>Operation Failed</H4><Paragraph>{error}</Paragraph></YStack>

  return <YStack flex={1} items="center" justify="center" gap="$4" p="$6"><H4>Security Consent</H4><Paragraph>{challenge?.provider_name} requests {challenge?.scope} access for {challenge?.purpose}.</Paragraph><Button theme="green" onPress={approve}>Approve with biometrics</Button><Button theme="red" onPress={deny}>Deny</Button></YStack>
}
