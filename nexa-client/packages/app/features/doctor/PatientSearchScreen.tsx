/**
 * Patient search screen — NFC resolve or manual patient ID entry.
 *
 * Two modes:
 *   1. NFC resolve: enter card UID → POST /api/v2/nfc/resolve
 *   2. Manual search: enter patient ID directly
 *
 * Handles merged-patient redirects:
 *   When the NFC resolve returns is_redirected=true, a warning banner
 *   is displayed and canonical_patient_id is used for navigation.
 *
 * Uses the shared NFC service via NexaApiClient / apiClient.
 * No hardcoded patient IDs, no local URLs.
 *
 * Route: /doctor/patient-search
 */

'use client'

import { Card, Text, YStack, Button, Input, XStack, Spinner, Paragraph, Separator } from '@my/ui'
import { useState, useEffect, useCallback } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { resolveNfcCard, NfcResolveError } from '../../services/nfcResolve'
import { useProviderAuth } from './ProviderAuthContext'

type SearchMode = 'manual' | 'nfc'
type SearchStep = 'input' | 'resolved'

interface ResolvedPatient {
  patient_id: string
  canonical_patient_id: string | null
  is_redirected: boolean
}

export function PatientSearchScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const initialMode = searchParams.get('mode') === 'nfc' ? 'nfc' : 'manual'
  const { isAuthenticated } = useProviderAuth()

  const [mode, setMode] = useState<SearchMode>(initialMode)
  const [query, setQuery] = useState('')
  const [resolved, setResolved] = useState<ResolvedPatient | null>(null)
  const [step, setStep] = useState<SearchStep>('input')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Respect ?mode=nfc from dashboard NFC scan button
  useEffect(() => {
    if (searchParams.get('mode') === 'nfc') {
      setMode('nfc')
    }
  }, [searchParams])

  // ── Session guard ─────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack flex={1} bg="$background" justifyContent="center" alignItems="center" gap="$4">
        <Text fontSize={44}>🔒</Text>
        <Text fontSize={22} fontWeight="900" color="$color12" textAlign="center">Session Required</Text>
        <Paragraph textAlign="center" color="$color11">You must be logged in to search patients.</Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/login')}>Go to Login</Button>
      </YStack>
    )
  }

  // ── NFC resolve ────────────────────────────────────────────────────────

  const handleNfcResolve = useCallback(async () => {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const result = await resolveNfcCard(query.trim())
      setResolved({
        patient_id: result.patient_id,
        canonical_patient_id: result.canonical_patient_id,
        is_redirected: result.is_redirected,
      })
      setStep('resolved')
    } catch (err) {
      if (err instanceof NfcResolveError) {
        setError(err.message)
      } else {
        setError('Failed to resolve NFC card. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }, [query])

  // ── Manual search ──────────────────────────────────────────────────────

  const handleManualSearch = useCallback(() => {
    if (!query.trim()) return
    setError(null)
    setResolved({
      patient_id: query.trim(),
      canonical_patient_id: null,
      is_redirected: false,
    })
    setStep('resolved')
  }, [query])

  // ── Navigate to consent request ────────────────────────────────────────

  const handleRequestAccess = useCallback(() => {
    if (!resolved) return
    // If the patient was merged, always use canonical_patient_id
    const targetId = resolved.is_redirected && resolved.canonical_patient_id
      ? resolved.canonical_patient_id
      : resolved.patient_id
    router.push(`/doctor/request-consent?patient_id=${encodeURIComponent(targetId)}`)
  }, [resolved, router])

  const handleBack = useCallback(() => {
    setStep('input')
    setResolved(null)
    setError(null)
  }, [])

  // ── Render: Resolved patient ───────────────────────────────────────────

  if (step === 'resolved' && resolved) {
    const displayId = resolved.is_redirected && resolved.canonical_patient_id
      ? resolved.canonical_patient_id
      : resolved.patient_id

    return (
      <YStack flex={1} bg="$background" padding="$5" gap="$5" maxWidth={600} marginHorizontal="auto">
        <YStack>
          <Text fontSize={26} fontWeight="900" color="$color12">Patient Found</Text>
          <Paragraph color="$color11" fontSize={15}>
            {mode === 'nfc' ? 'NFC card resolved successfully.' : 'Patient identified.'}
          </Paragraph>
        </YStack>

        {/* Merged-patient redirect warning */}
        {resolved.is_redirected && (
          <Card padding="$4" backgroundColor="$orange4" borderWidth={2} borderColor="$orange8" gap="$2">
            <Text color="$orange10" fontSize={16} fontWeight="700">
              ⚠️ Patient Record Merged
            </Text>
            <Paragraph color="$orange9" fontSize={14} marginTop="$2">
              This card was previously linked to a patient record that has been
              merged. All operations will use the canonical patient ID.
            </Paragraph>
            <YStack marginTop="$2" gap="$1">
              <Text color="$color11" fontSize={12}>Original patient ID:</Text>
              <Text color="$color12" fontSize={14}>{resolved.patient_id}</Text>
              <Text color="$color11" fontSize={12} marginTop="$1">Canonical patient ID:</Text>
              <Text color="$orange10" fontSize={14} fontWeight="700">
                {resolved.canonical_patient_id}
              </Text>
            </YStack>
          </Card>
        )}

        {/* Patient ID display */}
        <Card padding="$4" backgroundColor="$backgroundHover" gap="$2">
          <Paragraph color="$color11" fontSize={12} textTransform="uppercase" letterSpacing={1}>
            Patient ID
          </Paragraph>
          <Text color="$color12" fontSize={18} fontWeight="600">
            {displayId}
          </Text>
        </Card>

        <XStack gap="$3">
          <Button theme="blue" size="$4" onPress={handleRequestAccess}>
            Request Access
          </Button>
          <Button size="$4" chromeless onPress={handleBack}>
            Search Again
          </Button>
        </XStack>
      </YStack>
    )
  }

  // ── Render: Search input ───────────────────────────────────────────────

  return (
    <YStack flex={1} bg="$background" padding="$5" gap="$5" maxWidth={700} marginHorizontal="auto">
      <YStack>
        <Text fontSize={26} fontWeight="900" color="$color12">Patient Search</Text>
        <Paragraph color="$color11" fontSize={15}>
          {mode === 'nfc'
            ? 'Enter the NFC card UID from the patient card.'
            : 'Enter a patient ID or name to find their record.'}
        </Paragraph>
      </YStack>

      {/* Mode toggle */}
      <XStack gap="$2">
        <Button
          size="$3"
          theme={mode === 'manual' ? 'blue' : undefined}
          onPress={() => setMode('manual')}
        >
          Manual Search
        </Button>
        <Button
          size="$3"
          theme={mode === 'nfc' ? 'blue' : undefined}
          onPress={() => setMode('nfc')}
        >
          NFC Scan
        </Button>
      </XStack>

      <Separator />

      {/* Search input */}
      <XStack gap="$3" alignItems="center">
        <Input
          flex={1}
          size="$4"
          value={query}
          onChangeText={setQuery}
          placeholder={mode === 'nfc' ? 'Enter NFC card UID...' : 'Patient ID, name, or phone...'}
          onSubmitEditing={mode === 'nfc' ? handleNfcResolve : handleManualSearch}
          returnKeyType="search"
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Button
          theme="blue"
          size="$4"
          disabled={loading || !query.trim()}
          onPress={mode === 'nfc' ? handleNfcResolve : handleManualSearch}
        >
          {mode === 'nfc' ? 'Resolve' : 'Search'}
        </Button>
      </XStack>

      {loading && (
        <YStack alignItems="center" padding="$4">
          <Spinner size="large" color="$blue10" />
          {mode === 'nfc' && (
            <Paragraph color="$color11" fontSize={14} marginTop="$2">Resolving NFC card...</Paragraph>
          )}
        </YStack>
      )}

      {error && (
        <Card padding="$3" backgroundColor="$red4" borderRadius="$3">
          <Text color="$red10" fontSize={14}>{error}</Text>
        </Card>
      )}

      {/* NFC mode hint */}
      {mode === 'nfc' && !loading && !error && (
        <Card padding="$3" backgroundColor="$blue4" borderRadius="$3">
          <Text color="$blue10" fontSize={14}>
            ALPHA: NFC card UID is entered manually in this demo. Production will
            use native NFC tap.
          </Text>
        </Card>
      )}
    </YStack>
  )
}
