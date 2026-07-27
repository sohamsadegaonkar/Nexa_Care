'use client'

import { Button, Card, Input, Text, TextArea, YStack, XStack, Spinner, Sheet } from '@my/ui'
import { useState } from 'react'
import { mergePatients, MergeError } from '../../api/merge'
import { cancelMergeChallenge, createMergeChallenge, verifyMergeChallenge } from '../../api/auth'
import { ApiError } from '../../utils/apiClient'

export function MergeAdminScreen() {
  const [mfaCode, setMfaCode] = useState('')
  const [stepUpChallengeId, setStepUpChallengeId] = useState<string | null>(null)
  const [showMfaSheet, setShowMfaSheet] = useState(false)

  const [oldUuid, setOldUuid] = useState('')
  const [canonicalUuid, setCanonicalUuid] = useState('')
  const [reason, setReason] = useState('')
  const [evidence, setEvidence] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  const initiateMerge = async () => {
    if (!oldUuid || !canonicalUuid || !reason) {
      setError('All fields are required')
      return
    }

    setLoading(true)
    setError('')

    try {
      const res = await createMergeChallenge()
      setStepUpChallengeId(res.challenge_token)
      setShowMfaSheet(true)
    } catch (e: any) {
      setError('Failed to initiate merge challenge')
    } finally {
      setLoading(false)
    }
  }

  const handleVerifyMfa = async () => {
    if (!mfaCode || mfaCode.length !== 6 || !stepUpChallengeId) {
      setError('Please enter a 6-digit code')
      return
    }

    setLoading(true)
    setError('')

    try {
      const res = await verifyMergeChallenge(stepUpChallengeId, mfaCode)
      if (res.verified) {
        await executeMerge(stepUpChallengeId)
        setShowMfaSheet(false)
      }
    } catch (error: unknown) {
      setStepUpChallengeId(null)
      setMfaCode('')
      if (error instanceof ApiError && error.status === 410)
        setError('Challenge expired. Start again.')
      else if (error instanceof ApiError && error.status === 401)
        setError('MFA verification failed.')
      else if (error instanceof ApiError && error.status === 403)
        setError('This session cannot use that challenge.')
      else setError('MFA service is unavailable. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const executeMerge = async (token: string) => {
    setLoading(true)
    setError('')

    try {
      const res = await mergePatients(
        {
          old_patient_uuid: oldUuid.trim(),
          canonical_patient_uuid: canonicalUuid.trim(),
          reason: reason.trim(),
          evidence: evidence ? JSON.parse(evidence) : undefined,
        },
        token
      )
      setResult(res)
      setStepUpChallengeId(null)
      setMfaCode('')
    } catch (e: any) {
      if (e instanceof MergeError) {
        setError(e.message)
      } else {
        setError('Merge failed')
      }
      // If merge failed, MFA might need to be redone since it is single-use
      setStepUpChallengeId(null)
      setMfaCode('')
    } finally {
      setLoading(false)
    }
  }

  if (result) {
    return (
      <YStack
        flex={1}
        bg="$background"
        p="$6"
        gap="$4"
        items="center"
        justify="center"
      >
        <Text
          fontSize={24}
          fontWeight="900"
          color="$green11"
        >
          Merge Successful
        </Text>
        <Card
          p="$4"
          bg="$color2"
          width="100%"
          maxW={400}
        >
          <Text>Tombstone ID: {result.tombstone_id}</Text>
          <Text>Canonical: {result.canonical_patient_uuid}</Text>
        </Card>
        <Button onPress={() => window.location.reload()}>New Merge</Button>
      </YStack>
    )
  }

  return (
    <YStack
      flex={1}
      bg="$background"
      p="$5"
      gap="$5"
    >
      <Text
        fontSize={24}
        fontWeight="900"
        color="$color12"
      >
        Patient Merge (Admin)
      </Text>
      <Text color="$red11">Fresh MFA + Admin role required</Text>

      <YStack gap="$4">
        <Input
          placeholder="Old Patient UUID"
          value={oldUuid}
          onChangeText={setOldUuid}
        />
        <Input
          placeholder="Canonical Patient UUID"
          value={canonicalUuid}
          onChangeText={setCanonicalUuid}
        />
        <Input
          placeholder="Reason for merge"
          value={reason}
          onChangeText={setReason}
        />
        <TextArea
          placeholder="Evidence (JSON)"
          value={evidence}
          onChangeText={setEvidence}
          minHeight={100}
        />

        {error && <Text color="$red11">{error}</Text>}

        <Button
          theme="red"
          size="$5"
          disabled={loading}
          onPress={initiateMerge}
        >
          {loading ? <Spinner /> : 'INITIATE MERGE'}
        </Button>
      </YStack>

      <Sheet
        modal
        open={showMfaSheet}
        onOpenChange={(open) => {
          setShowMfaSheet(open)
          if (!open) {
            if (stepUpChallengeId) void cancelMergeChallenge(stepUpChallengeId)
            setStepUpChallengeId(null)
            setMfaCode('')
          }
        }}
        snapPoints={[40]}
        dismissOnSnapToBottom
      >
        <Sheet.Overlay />
        <Sheet.Frame
          p="$4"
          gap="$4"
        >
          <Sheet.Handle />
          <Text
            fontSize={20}
            fontWeight="900"
          >
            MFA Verification Required
          </Text>
          <Text color="$color11">Enter your 6-digit TOTP code to authorize this merge.</Text>
          <Input
            placeholder="123456"
            value={mfaCode}
            onChangeText={setMfaCode}
            keyboardType="numeric"
            maxLength={6}
          />
          <Button
            theme="blue"
            onPress={handleVerifyMfa}
            disabled={loading}
          >
            {loading ? <Spinner /> : 'Verify & Execute'}
          </Button>
          {error && <Text color="$red11">{error}</Text>}
        </Sheet.Frame>
      </Sheet>
    </YStack>
  )
}
