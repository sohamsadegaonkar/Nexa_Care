'use client'

import { Button, Card, Input, Paragraph, Spinner, Text, XStack, YStack } from '@my/ui'
import { useCallback, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { resolveNfcCard } from '../../services/nfcResolve'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'

type SearchMode = 'manual' | 'nfc'

/** Stores only an opaque discovery capability in provider memory. */
export function PatientSearchScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [mode, setMode] = useState<SearchMode>(searchParams.get('mode') === 'nfc' ? 'nfc' : 'manual')
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { isAuthenticated, session, setDiscoverySelection } = useProviderAuth()
  const documentUploadIntent = searchParams.get('intent') === 'document_upload'
  const resolve = useCallback(async () => {
    const input = value.trim(); const hospitalId = session?.hospital.hospital_id
    if (!input || !hospitalId) return
    setLoading(true); setError(null)
    try {
      const result = mode === 'nfc' ? await resolveNfcCard(input) : await NexaApiClient.discoverPatient({ identifier_type: 'NEXA_PUBLIC_ID', value: input }, hospitalId)
      setDiscoverySelection({ discoveryHandle: result.discovery_handle, expiresAt: result.expires_at, displayIdentifier: mode === 'manual' ? input.toUpperCase() : 'NFC card', source: mode === 'manual' ? 'public_id' : 'nfc' })
      router.push(`/doctor/request-consent${documentUploadIntent ? '?intent=document_upload' : ''}`)
    } catch (caught) { setError(caught instanceof ApiError ? 'Patient could not be identified. Search again.' : 'Patient could not be identified. Search again.') }
    finally { setLoading(false) }
  }, [documentUploadIntent, mode, router, session?.hospital.hospital_id, setDiscoverySelection, value])
  if (!isAuthenticated) return <YStack flex={1} alignItems="center" justifyContent="center"><Text>Session Required</Text></YStack>
  return <YStack flex={1} padding="$5" gap="$5" maxWidth={700} marginHorizontal="auto">
    <YStack><Text fontSize={26} fontWeight="900">Find Patient</Text><Paragraph>{mode === 'nfc' ? 'Enter the NFC card UID.' : 'Enter the patient’s Nexa patient ID.'}</Paragraph></YStack>
    <XStack gap="$2"><Button theme={mode === 'manual' ? 'blue' : undefined} onPress={() => setMode('manual')}>Nexa Patient ID</Button><Button theme={mode === 'nfc' ? 'blue' : undefined} onPress={() => setMode('nfc')}>NFC Scan</Button></XStack>
    <XStack gap="$3"><Input flex={1} value={value} onChangeText={setValue} placeholder={mode === 'nfc' ? 'Enter NFC card UID...' : 'NC-...'} autoCapitalize="characters" autoCorrect={false} onSubmitEditing={resolve}/><Button theme="blue" disabled={loading || !value.trim()} onPress={resolve}>{loading ? <Spinner /> : 'Continue'}</Button></XStack>
    {error && <Card padding="$3" backgroundColor="$red4"><Text color="$red10">{error}</Text></Card>}
  </YStack>
}
