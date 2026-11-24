'use client'

import { Button, Paragraph, YStack } from '@my/ui'
import { useEffect, useRef, useState } from 'react'
import { NexaApiClient, type OriginalReadContext, type OriginalReceipt } from '../../utils/apiClient'

import { OriginalPdfPages } from './OriginalPdfPages'

type Props = { documentId: string; access: OriginalReadContext }
type Preview = { url: string; file: Blob }

export function OriginalDocumentViewer({ documentId, access }: Props) {
  const [receipt, setReceipt] = useState<OriginalReceipt | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const alive = useRef(true)
  const generation = useRef(0)
  const url = useRef('')
  const lease = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  function discard() {
    generation.current++
    clearTimeout(lease.current)
    if (url.current) URL.revokeObjectURL(url.current)
    url.current = ''
  }
  function close(text: string) {
    discard()
    setPreview(null)
    setReceipt(null)
    setBusy(false)
    setMessage(text)
  }
  useEffect(() => {
    alive.current = true
    const hide = () => { if (document.hidden) close('') }
    const leave = () => close('')
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('pagehide', leave)
    return () => {
      alive.current = false
      discard()
      document.removeEventListener('visibilitychange', hide)
      window.removeEventListener('pagehide', leave)
    }
  }, [])

  const { patientId, hospitalId, consentToken } = access
  const id = receipt?.request_id
  const expires = receipt?.expires_at
  useEffect(() => {
    if (!id || !expires) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const expiryTimer = setTimeout(() => close('Document authorization expired.'), Math.max(0, Date.parse(expires) - Date.now()))
    async function poll() {
      const version = generation.current
      try {
        const status = await NexaApiClient.originalStatus({ patientId, hospitalId, consentToken }, id!)
        if (cancelled || version !== generation.current) return
        if (['pending', 'approved', 'opened'].includes(status.status)) {
          setReceipt(status)
          if (url.current) {
            clearTimeout(lease.current)
            lease.current = setTimeout(() => close('Preview closed. Access could not be confirmed.'), 8000)
          }
          timer = setTimeout(poll, 3000)
        } else close('Document access ' + status.status + '.')
      } catch {
        if (!cancelled && version === generation.current) close('Document access is unavailable. Request access again.')
      }
    }
    timer = setTimeout(poll, 1500)
    return () => { cancelled = true; clearTimeout(timer); clearTimeout(expiryTimer) }
  }, [id, expires, patientId, hospitalId, consentToken])

  async function act(open: boolean) {
    if (busy) return
    const version = generation.current
    setBusy(true)
    setMessage('')
    try {
      if (open && receipt) {
        const blob = await NexaApiClient.openOriginal(access, receipt.request_id)
        const status = await NexaApiClient.originalStatus(access, receipt.request_id)
        if (!alive.current || version !== generation.current) return
        if (status.status !== 'opened' || Date.parse(status.expires_at) <= Date.now() ||
          !['application/pdf', 'image/jpeg', 'image/png'].includes(blob.type)) throw new Error()
        url.current = URL.createObjectURL(blob)
        setPreview({ url: url.current, file: blob })
        lease.current = setTimeout(() => close('Preview closed. Access could not be confirmed.'), 8000)
        setReceipt(status)
      } else {
        const created = await NexaApiClient.requestOriginal(access, documentId)
        if (alive.current && version === generation.current) setReceipt(created)
      }
    } catch {
      if (alive.current && version === generation.current) close('Original unavailable. Check your access and retry.')
    } finally {
      if (alive.current && version === generation.current) setBusy(false)
    }
  }
  return <YStack gap="$2">
    {!receipt && <Button disabled={busy} onPress={() => void act(false)}>Request original document</Button>}
    {receipt?.status === 'pending' && <Paragraph>Waiting for the patient to approve this document.</Paragraph>}
    {receipt?.status === 'approved' && <Button disabled={busy} onPress={() => void act(true)}>Open approved original</Button>}
    {busy && <Paragraph>Loading…</Paragraph>}
    {message && <Paragraph>{message}</Paragraph>}
    {preview && (preview.file.type === 'application/pdf'
      ? <OriginalPdfPages file={preview.file} />
      : <img alt="Original medical report" src={preview.url} style={{ maxWidth: '100%', objectFit: 'contain' }} />)}
    {receipt && <Button onPress={() => close('')}>Close document access</Button>}
  </YStack>
}
