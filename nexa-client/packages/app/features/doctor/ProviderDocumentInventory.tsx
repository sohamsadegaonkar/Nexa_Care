'use client'

import { Button, Card, Paragraph, Spinner, Text, XStack, YStack } from '@my/ui'
import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  NexaApiClient,
  type ProviderDocumentItem,
} from '../../utils/apiClient'

interface Props {
  patientId: string
  consentToken: string
  hospitalId: string
  onAuthorizationFailure: () => void
}

/** Canonical reference inventory; NOT an original-source document viewer. */
export function ProviderDocumentInventory({
  patientId,
  consentToken,
  hospitalId,
  onAuthorizationFailure,
}: Props) {
  const [items, setItems] = useState<ProviderDocumentItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const requestVersion = useRef(0)

  const fetchPage = async (next: string | null, version: number) => {
    setLoading(true)
    setError(null)
    try {
      const page = await NexaApiClient.getProviderPatientDocuments(
        patientId, consentToken, hospitalId, next
      )
      if (requestVersion.current !== version) return
      setItems((previous) => next ? [...previous, ...page.items] : page.items)
      setCursor(page.next_cursor)
    } catch (caught) {
      if (requestVersion.current !== version) return
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) {
        setItems([])
        setCursor(null)
        onAuthorizationFailure()
        return
      }
      setError('Document references could not be loaded. The available record inventory is unknown.')
    } finally {
      if (requestVersion.current === version) setLoading(false)
    }
  }

  useEffect(() => {
    const version = ++requestVersion.current
    setItems([])
    setCursor(null)
    void fetchPage(null, version)
    return () => {
      requestVersion.current += 1
    }
    // Patient identity, provider session, and memory-only capability changes
    // are the triggers; an unstable parent callback must not force refetches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patientId, consentToken, hospitalId])

  const retry = () => {
    if (loading) return
    const version = ++requestVersion.current
    void fetchPage(null, version)
  }

  const loadOlder = () => {
    if (loading || !cursor) return
    const version = ++requestVersion.current
    void fetchPage(cursor, version)
  }

  return (
    <YStack gap="$3">
      <Paragraph color="$color11" fontSize={13}>
        These are record references from the patient's authorized Nexa Care history,
        not a complete inventory of every hospital the patient has visited.
        An attached or patient-reviewed report is not clinician verified.
      </Paragraph>

      {items.map((document) => (
        <Card
          key={document.document_id}
          borderWidth={1}
          borderColor="$color5"
          borderRadius="$3"
          backgroundColor="$color2"
          padding="$3"
          gap="$2"
        >
          <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
            <Text color="$color12" fontWeight="700">
              {document.document_type.replace(/_/g, ' ')}
            </Text>
            <Text color="$color10" fontSize={12}>
              {document.uploaded_at.slice(0, 10)}
            </Text>
          </XStack>
          <Paragraph color="$color11" fontSize={13}>
            {document.source_origin === 'patient_imported_external'
              ? 'Source: Patient-imported external record'
              : 'Source origin: Not established'}
          </Paragraph>
          <Paragraph color="$color11" fontSize={13}>
            {document.verification_status === 'patient_reviewed_not_clinician_verified'
              ? 'Patient reviewed · Not clinician verified'
              : 'Clinical verification: Not established'}
          </Paragraph>
          <Text color="$color10" fontSize={12}>
            Original source: separate authorization required
          </Text>
        </Card>
      ))}

      {loading && (
        <XStack gap="$2" alignItems="center">
          <Spinner />
          <Paragraph color="$color11">
            Loading authorized document references…
          </Paragraph>
        </XStack>
      )}

      {error && (
        <YStack gap="$2">
          <Paragraph color="$red10">{error}</Paragraph>
          <Button onPress={retry} disabled={loading}>Retry document list</Button>
        </YStack>
      )}

      {!loading && !error && items.length === 0 && (
        <Paragraph color="$color11">
          No document references recorded in the available Nexa Care history.
        </Paragraph>
      )}

      {!loading && !error && cursor && (
        <Button onPress={loadOlder} disabled={loading}>Load older documents</Button>
      )}
    </YStack>
  )
}
