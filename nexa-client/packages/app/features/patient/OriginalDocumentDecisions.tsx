import React, { useEffect, useRef, useState } from 'react'
import { Button, Paragraph, YStack } from 'tamagui'
import { NexaApiClient, type OriginalDecisionCard } from '../../utils/apiClient'
import { ensureCurrentDeviceEnrollment } from '../../services/currentDeviceEnrollment'
import { authenticateWithBiometrics } from '../../services/deviceKeys'
import { signWithNativeDeviceKey } from '../../services/nativeDeviceSecurity'

export function OriginalDocumentDecisions() {
  const [cards, setCards] = useState<OriginalDecisionCard[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const version = useRef(0)
  async function refresh() {
    const current = ++version.current
    try {
      const rows = await NexaApiClient.myOriginalRequests()
      if (current === version.current) { setCards(rows); setMessage('') }
    } catch {
      if (current === version.current) { setCards([]); setMessage('Document requests could not be loaded.') }
    }
  }
  useEffect(() => { void refresh(); return () => { version.current++ } }, [])
  async function decide(card: OriginalDecisionCard, decision: 'approved' | 'denied' | 'revoked') {
    setBusy(true)
    try {
      if (decision === 'revoked') await NexaApiClient.revokeOriginal(card.request_id)
      else {
        const ctx = card.context
        if (ctx.protocol_version !== 'nexa-original-document-v1' || ctx.operation !== 'READ_DOCUMENTS' ||
          ctx.request_id !== card.request_id || ctx.purpose !== 'review_original_for_current_care' ||
          !ctx.document_id || Date.parse(ctx.expires_at) <= Date.now()) throw new Error()
        const device = await ensureCurrentDeviceEnrollment({ allowEnrollment: false })
        await authenticateWithBiometrics()
        const fields = { ...ctx, decision, device_id: device.deviceId, key_version: device.keyVersion,
          key_fingerprint: device.keyFingerprint, key_id: device.keyId }
        const signature = await signWithNativeDeviceKey(device.keyAlias, JSON.stringify(fields, Object.keys(fields).sort()))
        await NexaApiClient.decideOriginal({ request_id: card.request_id, decision, key_id: device.keyId, signature })
      }
      await refresh()
    } catch { setMessage('The change could not be confirmed. Check your enrolled device and refresh.') }
    finally { setBusy(false) }
  }
  return <YStack gap="$2">
    <Paragraph fontWeight="700">Original document access</Paragraph>
    <Button disabled={busy} onPress={() => void refresh()}>Refresh document requests</Button>
    {message && <Paragraph>{message}</Paragraph>}
    {cards.map(card => <YStack key={card.request_id} gap="$1">
      <Paragraph>{card.provider || 'Provider ' + card.context.provider_id} · {card.hospital}</Paragraph>
      <Paragraph>{card.category} · Uploaded {card.uploaded_at.slice(0, 10)} · Document {card.context.document_id}</Paragraph>
      <Paragraph>Review the original for your current care. One opening, until {new Date(card.expires_at).toLocaleString()}. Status: {card.status}.</Paragraph>
      {card.status === 'pending' && <>
        <Button disabled={busy} onPress={() => void decide(card, 'approved')}>Allow this original</Button>
        <Button disabled={busy} onPress={() => void decide(card, 'denied')}>Deny this request</Button>
      </>}
      {['approved', 'opened'].includes(card.status) && <Button disabled={busy} onPress={() => void decide(card, 'revoked')}>Revoke document access</Button>}
    </YStack>)}
  </YStack>
}
