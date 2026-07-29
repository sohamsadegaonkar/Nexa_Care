'use client'

import { Paragraph, Spinner, YStack } from '@my/ui'
import { useEffect, useState } from 'react'
import { ApiError, NexaApiClient } from '../../utils/apiClient'

export function ProtectedSourceViewer({
  caseId,
  reviewSessionId,
  onTerminalAccessFailure,
}: {
  caseId: string
  reviewSessionId: string
  onTerminalAccessFailure: () => void
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [contentType, setContentType] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    let createdUrl: string | null = null
    setError(null)
    setObjectUrl(null)
    setContentType(null)
    void NexaApiClient.getAdjudicationSource(caseId, reviewSessionId)
      .then((blob) => {
        if (!active) return
        createdUrl = URL.createObjectURL(blob)
        setContentType(blob.type)
        setObjectUrl(createdUrl)
      })
      .catch((reason: unknown) => {
        if (!active) return
        if (
          reason instanceof ApiError &&
          [
            'ADJUDICATION_SESSION_MISMATCH',
            'ADJUDICATION_CONSENT_INACTIVE',
            'ADJUDICATION_ERASURE_ACCESS_BLOCKED',
            'ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE',
            'FORBIDDEN',
          ].includes(reason.code ?? '')
        ) {
          onTerminalAccessFailure()
          setError('Source access expired. Reopen the review through the authorized workflow.')
          return
        }
        setError('The protected source document could not be loaded.')
      })
    return () => {
      active = false
      if (createdUrl) URL.revokeObjectURL(createdUrl)
    }
  }, [caseId, reviewSessionId, onTerminalAccessFailure])

  if (error) {
    return (
      <YStack
        padding="$4"
        role="alert"
      >
        <Paragraph color="$red10">{error}</Paragraph>
      </YStack>
    )
  }
  if (!objectUrl || !contentType) {
    return (
      <YStack
        minHeight={400}
        alignItems="center"
        justifyContent="center"
        gap="$3"
      >
        <Spinner size="large" />
        <Paragraph>Loading protected source…</Paragraph>
      </YStack>
    )
  }
  if (contentType === 'application/pdf') {
    return (
      <object
        aria-label="Protected source document"
        data={objectUrl}
        type="application/pdf"
        style={{ width: '100%', minHeight: 640 }}
      >
        <Paragraph>The browser could not render this protected PDF.</Paragraph>
      </object>
    )
  }
  if (contentType.startsWith('image/')) {
    return (
      <img
        src={objectUrl}
        alt="Protected source document"
        style={{ maxWidth: '100%', height: 'auto', display: 'block', margin: '0 auto' }}
      />
    )
  }
  return (
    <YStack padding="$4">
      <Paragraph>
        This source format cannot be displayed in the browser. No document was sent to an external
        viewer.
      </Paragraph>
    </YStack>
  )
}
