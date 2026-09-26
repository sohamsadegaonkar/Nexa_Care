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
import {
  ArrowRight,
  Phone,
  QrCode,
  RadioReceiver,
  Search,
  ShieldCheck,
} from '@tamagui/lucide-icons'
import { useCallback, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { discoverPatientExact, type DiscoveryIdentifierType } from '../../services/patientDiscovery'
import { resolveNfcCard } from '../../services/nfcResolve'
import { ApiError } from '../../utils/apiClient'
import { useProviderAuth, type PatientDiscoverySelection } from './ProviderAuthContext'

type SearchMode = 'public_id' | 'phone' | 'qr' | 'nfc'

function initialMode(value: string | null): SearchMode {
  return value === 'nfc' || value === 'phone' || value === 'qr' ? value : 'public_id'
}

function identifierType(mode: Exclude<SearchMode, 'nfc'>): DiscoveryIdentifierType {
  if (mode === 'phone') return 'PHONE'
  if (mode === 'qr') return 'QR_PUBLIC_ID'
  return 'NEXA_PUBLIC_ID'
}

function safeDisplay(mode: SearchMode, input: string): Pick<PatientDiscoverySelection, 'displayIdentifier' | 'source'> {
  if (mode === 'phone') return { displayIdentifier: 'Verified phone match', source: 'phone' }
  if (mode === 'qr') return { displayIdentifier: 'Nexa QR code', source: 'qr' }
  if (mode === 'nfc') return { displayIdentifier: 'NFC card', source: 'nfc' }
  return { displayIdentifier: input.toUpperCase(), source: 'public_id' }
}

function discoveryError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return 'Patient search could not be completed. Check the details or try another method.'
  }
  if (error.code === 'CLINICAL_ELIGIBILITY_DENIED') {
    return 'Patient search is not currently available for this provider account. Review provider verification status or contact your clinical administrator.'
  }
  if (
    error.code === 'DISCOVERY_RECENT_MFA_REQUIRED' ||
    error.code === 'CLINICAL_MFA_REQUIRED' ||
    error.code === 'RECENT_MFA_REQUIRED'
  ) {
    return 'Additional verification is required before using this search method. Complete MFA and try again.'
  }
  if (error.code === 'DISCOVERY_NO_MATCH') {
    return 'No patient could be matched with the information provided. Check the details or use another search method.'
  }
  if (error.code === 'DISCOVERY_RATE_LIMITED' || error.status === 429) {
    return 'Patient search is temporarily limited after repeated attempts. Wait briefly, then try again.'
  }
  if (error.code === 'DISCOVERY_HANDLE_INVALID') {
    return 'The patient selection expired. Find the patient again.'
  }
  if (
    error.code === 'DISCOVERY_UNAVAILABLE' ||
    error.code === 'DISCOVERY_SECURITY_CONTROL_UNAVAILABLE' ||
    error.status >= 500 ||
    error.status === 0
  ) {
    return 'Patient search is temporarily unavailable because a required service cannot be reached. Try again when the service recovers.'
  }
  if (error.status === 401) {
    return 'Your provider session expired. Sign in again before searching for a patient.'
  }
  if (error.status === 403) {
    return 'Your current clinical session is not authorized to search for patients. Re-authenticate or contact your clinical administrator.'
  }
  return 'Patient search could not be completed. Check the details or try another method.'
}

/**
 * Stores only an opaque discovery capability in provider memory.
 * Patient search values and capabilities never enter URLs or durable storage.
 */
export function PatientSearchScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [mode, setMode] = useState<SearchMode>(initialMode(searchParams.get('mode')))
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { isAuthenticated, session, setDiscoverySelection } = useProviderAuth()
  const documentUploadIntent = searchParams.get('intent') === 'document_upload'
  const treatmentVitalsIntent = searchParams.get('intent') === 'treatment_vitals'

  const selectMode = useCallback((next: SearchMode) => {
    setMode(next)
    setValue('')
    setError(null)
  }, [])

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
          : await discoverPatientExact(
              { identifier_type: identifierType(mode), value: input },
              hospitalId
            )
      const display = safeDisplay(mode, input)
      // Clear the searched value before navigation. Only the opaque capability
      // and a non-sensitive display label remain in provider memory.
      setValue('')
      setDiscoverySelection({
        discoveryHandle: result.discovery_handle,
        expiresAt: result.expires_at,
        ...display,
      })
      if (treatmentVitalsIntent) {
        router.push('/doctor/treatment-vitals')
      } else {
        router.push(`/doctor/request-consent${documentUploadIntent ? '?intent=document_upload' : ''}`)
      }
    } catch (caught) {
      setError(discoveryError(caught))
    } finally {
      setLoading(false)
    }
  }, [
    documentUploadIntent,
    mode,
    router,
    session?.hospital.hospital_id,
    setDiscoverySelection,
    treatmentVitalsIntent,
    value,
  ])

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
          treatmentVitalsIntent
            ? 'Identify the patient before requesting an operation-bound Treatment Session for vitals entry.'
            : documentUploadIntent
              ? 'Identify the patient before requesting document processing and clinical upload consent.'
              : 'Locate the patient to initiate a scoped consent request.'
        }
      />

      <XStack gap="$3" flexWrap="wrap">
        <ActionButton
          flex={1}
          minWidth={180}
          intent={mode === 'public_id' ? 'primary' : undefined}
          onPress={() => selectMode('public_id')}
        >
          <XStack alignItems="center" gap="$2">
            <Search size={18} color={mode === 'public_id' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'public_id' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              Nexa Patient ID
            </Text>
          </XStack>
        </ActionButton>

        <ActionButton
          flex={1}
          minWidth={180}
          intent={mode === 'phone' ? 'primary' : undefined}
          onPress={() => selectMode('phone')}
        >
          <XStack alignItems="center" gap="$2">
            <Phone size={18} color={mode === 'phone' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'phone' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              Verified Phone
            </Text>
          </XStack>
        </ActionButton>

        <ActionButton
          flex={1}
          minWidth={180}
          intent={mode === 'qr' ? 'primary' : undefined}
          onPress={() => selectMode('qr')}
        >
          <XStack alignItems="center" gap="$2">
            <QrCode size={18} color={mode === 'qr' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'qr' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              Nexa QR
            </Text>
          </XStack>
        </ActionButton>

        <ActionButton
          flex={1}
          minWidth={180}
          intent={mode === 'nfc' ? 'primary' : undefined}
          onPress={() => selectMode('nfc')}
        >
          <XStack alignItems="center" gap="$2">
            <RadioReceiver size={18} color={mode === 'nfc' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'nfc' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              NFC Scan
            </Text>
          </XStack>
        </ActionButton>
      </XStack>

      <Surface padding="$5" gap="$4">
        {mode === 'public_id' && (
          <YStack gap="$3">
            <XStack justifyContent="space-between" alignItems="center">
              <SectionHeading>Enter Nexa Patient Identifier</SectionHeading>
              <StatusBadge tone="info">Privacy Protected</StatusBadge>
            </XStack>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Enter the public identifier shown on the patient's Nexa Care app or printed health card.
            </Paragraph>
            <SearchInputField
              id="patient-search-id"
              label="Patient Public ID"
              placeholder="NC-..."
              hint="Enter the Nexa Patient ID exactly as shown in the patient app or health card."
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
        )}

        {mode === 'phone' && (
          <YStack gap="$3">
            <XStack justifyContent="space-between" alignItems="center">
              <SectionHeading>Find by Phone</SectionHeading>
              <StatusBadge tone="warning">Additional verification may be requested</StatusBadge>
            </XStack>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Use the phone number the patient has chosen for Nexa Care. For privacy, search does not reveal why an entry cannot be matched.
            </Paragraph>
            <SearchInputField
              id="patient-search-phone"
              label="Patient Phone Number"
              placeholder="+91..."
              value={value}
              onChangeText={(text) => {
                setValue(text)
                if (error) setError(null)
              }}
              autoCapitalize="none"
              autoCorrect={false}
              disabled={loading}
              onSubmitEditing={resolve}
            />
          </YStack>
        )}

        {mode === 'qr' && (
          <YStack gap="$3">
            <XStack justifyContent="space-between" alignItems="center">
              <SectionHeading>Scan Nexa QR</SectionHeading>
              <StatusBadge tone="info">Patient QR</StatusBadge>
            </XStack>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Scan the QR shown in the patient's Nexa Care app or health card. Clinical access still requires patient authorization.
            </Paragraph>
            <SearchInputField
              id="patient-search-qr"
              label="Nexa QR Payload"
              placeholder="nexa://patient-discovery/v1/NC-..."
              value={value}
              onChangeText={(text) => {
                setValue(text)
                if (error) setError(null)
              }}
              autoCapitalize="none"
              autoCorrect={false}
              disabled={loading}
              onSubmitEditing={resolve}
            />
          </YStack>
        )}

        {mode === 'nfc' && (
          <YStack gap="$3">
            <XStack justifyContent="space-between" alignItems="center">
              <SectionHeading>Tap NFC Health Card</SectionHeading>
              <StatusBadge tone="success">Ready to Scan</StatusBadge>
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
                Or manually enter the hardware UID from the card below.
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

        {error && <InlineNotice title={error} tone="danger" />}

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

      <Surface backgroundColor="$nexaMuted" borderColor="$nexaBorder" padding="$4">
        <XStack gap="$3" alignItems="center">
          <ShieldCheck size={24} color="$nexaAccent" />
          <YStack gap="$1" flex={1}>
            <Text color="$nexaText" fontWeight="700" fontSize={14}>
              Private Patient Matching
            </Text>
            <Paragraph color="$nexaSecondary" fontSize={13} lineHeight={20}>
              Nexa Care matches the patient without exposing clinical information. Patient permission is still required before routine clinical records can be opened.
            </Paragraph>
          </YStack>
        </XStack>
      </Surface>
    </ScreenContainer>
  )
}
