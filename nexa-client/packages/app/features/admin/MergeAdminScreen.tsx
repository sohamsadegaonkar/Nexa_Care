'use client'

import { Button, Card, Input, Text, TextArea, YStack, XStack, Spinner } from '@my/ui'
import { useState } from 'react'
import { mergePatients, MergeError } from '../../api/merge'

export function MergeAdminScreen({ mfaToken }: { mfaToken?: string | null }) {
  const [mfaCode, setMfaCode] = useState('')
  const [mfaVerified, setMfaVerified] = useState(false)
  const [oldUuid, setOldUuid] = useState('')
  const [canonicalUuid, setCanonicalUuid] = useState('')
  const [reason, setReason] = useState('')
  const [evidence, setEvidence] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  const verifyMfa = async () => {
    if (!mfaCode || mfaCode.length !== 6) {
      setError('Please enter a 6-digit code')
      return
    }

    setLoading(true)
    setError('')

    try {
      // Use step-up MFA action verification (fresh TOTP code)
      const res = await fetch('http://localhost:8000/api/v2/auth/mfa/verify-action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          code: mfaCode,
        }),
      })

      if (res.ok) {
        setMfaVerified(true)
      } else {
        const data = await res.json()
        setError(data.detail || 'Invalid MFA code')
      }
    } catch (e) {
      setError('MFA verification failed')
    } finally {
      setLoading(false)
    }
  }

  const handleMerge = async () => {
    if (!mfaVerified) {
      setError('MFA verification required')
      return
    }
    if (!oldUuid || !canonicalUuid || !reason) {
      setError('All fields are required')
      return
    }

    setLoading(true)
    setError('')

    try {
      const res = await mergePatients({
        old_patient_uuid: oldUuid.trim(),
        canonical_patient_uuid: canonicalUuid.trim(),
        reason: reason.trim(),
        evidence: evidence ? JSON.parse(evidence) : undefined,
      })
      setResult(res)
    } catch (e: any) {
      if (e instanceof MergeError) {
        setError(e.message)
      } else {
        setError('Merge failed')
      }
    } finally {
      setLoading(false)
    }
  }

  if (result) {
    return (
      <YStack flex={1} bg="$background" p="$6" gap="$4" items="center" justify="center">
        <Text fontSize={24} fontWeight="900" color="$green11">Merge Successful</Text>
        <Card p="$4" bg="$color2" width="100%" maxW={400}>
          <Text>Tombstone ID: {result.tombstone_id}</Text>
          <Text>Canonical: {result.canonical_patient_uuid}</Text>
        </Card>
        <Button onPress={() => window.location.reload()}>New Merge</Button>
      </YStack>
    )
  }

  return (
    <YStack flex={1} bg="$background" p="$5" gap="$5">
      <Text fontSize={24} fontWeight="900" color="$color12">Patient Merge (Admin)</Text>
      <Text color="$red11">MFA + Clinical_Admin role required</Text>

      {!mfaVerified ? (
        <YStack gap="$4">
          <Text color="$color11">Enter MFA code to unlock merge</Text>
          <Input
            placeholder="123456"
            value={mfaCode}
            onChangeText={setMfaCode}
            keyboardType="numeric"
            maxLength={6}
          />
          <Button theme="blue" onPress={verifyMfa}>Verify MFA</Button>
          {error && <Text color="$red11">{error}</Text>}
        </YStack>
      ) : (
        <YStack gap="$4">
          <Text color="$green11">✓ MFA Verified — Admin access granted</Text>
          <Input placeholder="Old Patient UUID" value={oldUuid} onChangeText={setOldUuid} />
          <Input placeholder="Canonical Patient UUID" value={canonicalUuid} onChangeText={setCanonicalUuid} />
          <Input placeholder="Reason for merge" value={reason} onChangeText={setReason} />
          <TextArea placeholder="Evidence (JSON)" value={evidence} onChangeText={setEvidence} minHeight={100} />

          {error && <Text color="$red11">{error}</Text>}

          <Button
            theme="red"
            size="$5"
            disabled={loading}
            onPress={handleMerge}
          >
            {loading ? <Spinner /> : 'EXECUTE MERGE'}
          </Button>
        </YStack>
      )}
    </YStack>
  )
}
