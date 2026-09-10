'use client'

import {
  ActionButton,
  FormField as SearchInputField,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenContainer,
  ScreenHeader,
  SectionHeading,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { Search, RadioReceiver, ShieldCheck, ArrowRight } from '@tamagui/lucide-icons'
import { useCallback, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { resolveNfcCard } from '../../services/nfcResolve'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'

type SearchMode = 'manual' | 'nfc'

/**
 * Stores only an opaque discovery capability in provider memory.
 * Never leaks patient identifiers or capabilities into URLs.
 */
export function PatientSearchScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [mode, setMode] = useState<SearchMode>(
    searchParams.get('mode') === 'nfc' ? 'nfc' : 'manual'
  )
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { isAuthenticated, session, setDiscoverySelection } = useProviderAuth()
  const documentUploadIntent = searchParams.get('intent') === 'document_upload'

  const resolve = useCallback(async () => {
    const input = value.trim()
    const hospitalId = session?.hospital.hospital_id
    if (!input || !hospitalId) return

    setLoading(true)
    setError(null)
    try {
      const result =
        mode === 'nfc'
          ? await resolveNfcCard(input)
          : await NexaApiClient.discoverPatient(
              { identifier_type: 'NEXA_PUBLIC_ID', value: input },
              hospitalId
            )
      setDiscoverySelection({
        discoveryHandle: result.discovery_handle,
        expiresAt: result.expires_at,
        displayIdentifier: mode === 'manual' ? input.toUpperCase() : 'NFC card',
        source: mode === 'manual' ? 'public_id' : 'nfc',
      })
      router.push(`/doctor/request-consent${documentUploadIntent ? '?intent=document_upload' : ''}`)
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message || 'Patient could not be identified. Please verify the ID and try again.'
          : 'Patient could not be identified. Please verify the ID and try again.'
      )
    } finally {
      setLoading(false)
    }
  }, [documentUploadIntent, mode, router, session?.hospital.hospital_id, setDiscoverySelection, value])

  if (!isAuthenticated) {
    return (
      <ScreenContainer>
        <Surface padding="$6" alignItems="center" gap="$4">
          <Text fontSize={20} fontWeight="800">Session Required</Text>
          <Paragraph color="$nexaSecondary">
            You must be signed in with an active provider session to discover patients.
          </Paragraph>
          <ActionButton onPress={() => router.push('/doctor/login')}>
            Sign In
          </ActionButton>
        </Surface>
      </ScreenContainer>
    )
  }

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="PATIENT DISCOVERY"
        title="Find Patient"
        description={
          documentUploadIntent
            ? 'Identify the patient before requesting document processing and clinical upload consent.'
            : 'Locate the patient to initiate a scoped consent request.'
        }
      />

      {/* Mode Selection Tabs */}
      <XStack gap="$3" flexWrap="wrap">
        <ActionButton
          flex={1}
          minWidth={200}
          intent={mode === 'manual' ? 'primary' : undefined}
          onPress={() => {
            setMode('manual')
            setError(null)
          }}
        >
          <XStack alignItems="center" gap="$2">
            <Search size={18} color={mode === 'manual' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text
              color={mode === 'manual' ? '$nexaOnAccent' : '$nexaText'}
              fontWeight="700"
            >
              Nexa Patient ID
            </Text>
          </XStack>
        </ActionButton>

        <ActionButton
          flex={1}
          minWidth={200}
          intent={mode === 'nfc' ? 'primary' : undefined}
          onPress={() => {
            setMode('nfc')
            setError(null)
          }}
        >
          <XStack alignItems="center" gap="$2">
            <RadioReceiver size={18} color={mode === 'nfc' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text
              color={mode === 'nfc' ? '$nexaOnAccent' : '$nexaText'}
              fontWeight="700"
            >
              NFC Scan
            </Text>
          </XStack>
        </ActionButton>
      </XStack>

      {/* Main Search Surface */}
      <Surface padding="$5" gap="$4">
        {mode === 'manual' ? (
          <YStack gap="$3">
            <XStack justifyContent="space-between" alignItems="center">
              <SectionHeading>Enter Nexa Patient Identifier</SectionHeading>
              <StatusBadge tone="info">Opaque Resolution</StatusBadge>
            </XStack>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Enter the patient's public identifier shown on their Nexa Care app or printed health card.
            </Paragraph>
            <SearchInputField
              id="patient-search-id"
              label="Patient Public ID"
              placeholder="NC-..."
              hint="Format: NC- followed by 24 hexadecimal characters (e.g. from card or app)."
              value={value}
              onChangeText={(text) => {
                setValue(text.toUpperCase())
                if (error) setError(null)
              }}
              autoCapitalize="characters"
              autoCorrect={false}
              disabled={loading}
              onSubmitEditing={resolve}
            />
          </YStack>
        ) : (
          <YStack gap="$3">
            <XStack justifyContent="space-between" alignItems="center">
              <SectionHeading>Tap NFC Health Card</SectionHeading>
              <StatusBadge tone="success">Contactless Ready</StatusBadge>
            </XStack>
            <YStack
              backgroundColor="$nexaAccentSoft"
              borderRadius={12}
              padding="$4"
              alignItems="center"
              gap="$2"
              borderWidth={1}
              borderColor="$nexaAccent"
            >
              <RadioReceiver size={36} color="$nexaAccent" />
              <Text color="$nexaAccent" fontWeight="800" fontSize={16}>
                Hold patient card to the NFC reader
              </Text>
              <Paragraph color="$nexaSecondary" fontSize={13} textAlign="center">
                Or manually enter the hardware UID from the card below:
              </Paragraph>
            </YStack>
            <SearchInputField
              id="patient-search-nfc"
              label="NFC Card UID"
              placeholder="Enter NFC card UID..."
              value={value}
              onChangeText={(text) => {
                setValue(text)
                if (error) setError(null)
              }}
              autoCapitalize="characters"
              autoCorrect={false}
              disabled={loading}
              onSubmitEditing={resolve}
            />
          </YStack>
        )}

        {error && (
          <InlineNotice title={error} tone="danger" />
        )}

        <XStack justifyContent="flex-end" gap="$3">
          <ActionButton
            intent="primary"
            disabled={loading || !value.trim()}
            aria-busy={loading}
            onPress={resolve}
          >
            {loading ? (
              <LoadingState label="Resolving patient…" />
            ) : (
              <XStack alignItems="center" gap="$2">
                <Text color="$nexaOnAccent" fontWeight="700">
                  Continue to Request Consent
                </Text>
                <ArrowRight size={16} color="$nexaOnAccent" />
              </XStack>
            )}
          </ActionButton>
        </XStack>
      </Surface>

      {/* Security Assurance Notice */}
      <Surface backgroundColor="$nexaMuted" borderColor="$nexaBorder" padding="$4">
        <XStack gap="$3" alignItems="center">
          <ShieldCheck size={24} color="$nexaAccent" />
          <YStack gap="$1" flex={1}>
            <Text color="$nexaText" fontWeight="700" fontSize={14}>
              Privacy & Zero-Knowledge Resolution
            </Text>
            <Paragraph color="$nexaSecondary" fontSize={13} lineHeight={20}>
              Patient resolution returns an encrypted, single-use discovery handle. No clinical data or medical history is fetched or exposed until the patient grants explicit permission on their trusted device.
            </Paragraph>
          </YStack>
        </XStack>
      </Surface>
    </ScreenContainer>
  )
}
