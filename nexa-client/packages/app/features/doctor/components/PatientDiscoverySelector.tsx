'use client'

import {
  ActionButton,
  Card,
  FormField as SearchInputField,
  InlineNotice,
  LoadingState,
  Paragraph,
  SectionHeading,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import {
  ArrowRight,
  CheckCircle,
  Phone,
  QrCode,
  RadioReceiver,
  Search,
  ShieldCheck,
} from '@tamagui/lucide-icons'
import { useCallback, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  discoverPatientExact,
  type DiscoveryIdentifierType,
} from '../../../services/patientDiscovery'
import { resolveNfcCard } from '../../../services/nfcResolve'
import { ApiError, NexaApiClient } from '../../../utils/apiClient'
import {
  useProviderAuth,
  type PatientDiscoverySelection,
} from '../ProviderAuthContext'

export type DiscoverySearchMode = 'public_id' | 'phone' | 'qr' | 'nfc'
export type DiscoveryContextType = 'normal' | 'document' | 'treatment' | 'emergency'

export interface PatientDiscoveryResult {
  discoveryHandle: string
  expiresAt: string
  displayIdentifier: string
  source: 'public_id' | 'phone' | 'qr' | 'nfc'
}

interface PatientDiscoverySelectorProps {
  initialMode?: DiscoverySearchMode
  context?: DiscoveryContextType
  onIdentified?: (result: PatientDiscoveryResult) => void
  onReset?: () => void
  identifiedResult?: PatientDiscoveryResult | null
  compact?: boolean
}

function identifierType(mode: Exclude<DiscoverySearchMode, 'nfc'>): DiscoveryIdentifierType {
  if (mode === 'phone') return 'PHONE'
  if (mode === 'qr') return 'QR_PUBLIC_ID'
  return 'NEXA_PUBLIC_ID'
}

function friendlySourceLabel(source: 'public_id' | 'phone' | 'qr' | 'nfc'): string {
  if (source === 'phone') return 'Phone'
  if (source === 'qr') return 'Nexa QR'
  if (source === 'nfc') return 'NFC Card'
  return 'Nexa ID'
}

function safeDisplay(
  mode: DiscoverySearchMode,
  input: string
): Pick<PatientDiscoverySelection, 'displayIdentifier' | 'source'> {
  if (mode === 'phone') return { displayIdentifier: 'Verified phone match', source: 'phone' }
  if (mode === 'qr') return { displayIdentifier: 'Nexa QR code', source: 'qr' }
  if (mode === 'nfc') return { displayIdentifier: 'NFC card', source: 'nfc' }
  return { displayIdentifier: input.toUpperCase(), source: 'public_id' }
}

function mapDiscoveryError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "We couldn't find a patient using those details. Check the information or try Nexa ID, QR or NFC."
  }
  if (error.code === 'DISCOVERY_RATE_LIMITED') {
    return 'Too many searches were attempted. Try again shortly.'
  }
  if (error.code === 'CLINICAL_MFA_ENROLLMENT_REQUIRED') {
    return 'Authenticator setup is required before clinical access can be used.'
  }
  if (error.code === 'CLINICAL_ELIGIBILITY_DENIED') {
    return 'Clinical access is not currently available for this account. Contact your hospital administrator.'
  }
  if (error.status >= 500 || error.code === 'DISCOVERY_UNAVAILABLE') {
    return 'Patient search is temporarily unavailable. Try again.'
  }
  return "We couldn't find a patient using those details. Check the information or try Nexa ID, QR or NFC."
}

export function PatientDiscoverySelector({
  initialMode = 'public_id',
  context = 'normal',
  onIdentified,
  onReset,
  identifiedResult: controlledIdentified,
  compact = false,
}: PatientDiscoverySelectorProps) {
  const router = useRouter()
  const { session, setDiscoverySelection } = useProviderAuth()

  const [mode, setMode] = useState<DiscoverySearchMode>(initialMode)
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Step-up authentication state for phone discovery
  const [needsStepUp, setNeedsStepUp] = useState(false)
  const [stepUpCode, setStepUpCode] = useState('')
  const [stepUpLoading, setStepUpLoading] = useState(false)
  const [pendingPhone, setPendingPhone] = useState('')

  // Secondary manual entry toggles
  const [showManualQr, setShowManualQr] = useState(false)
  const [showManualNfc, setShowManualNfc] = useState(false)

  // Local identified result if not controlled externally
  const [localIdentified, setLocalIdentified] = useState<PatientDiscoveryResult | null>(null)
  const identified = controlledIdentified !== undefined ? controlledIdentified : localIdentified

  const selectMode = useCallback((next: DiscoverySearchMode) => {
    setMode(next)
    setValue('')
    setError(null)
    setNeedsStepUp(false)
    setStepUpCode('')
    setPendingPhone('')
  }, [])

  const executeDiscovery = useCallback(
    async (searchValue: string, targetMode: DiscoverySearchMode) => {
      const input = searchValue.trim()
      const hospitalId = session?.hospital.hospital_id
      if (!input || !hospitalId) return

      setLoading(true)
      setError(null)
      try {
        const result =
          targetMode === 'nfc'
            ? await resolveNfcCard(input)
            : await discoverPatientExact(
                { identifier_type: identifierType(targetMode), value: input },
                hospitalId
              )

        const display = safeDisplay(targetMode, input)
        setValue('')
        setNeedsStepUp(false)
        setStepUpCode('')
        setPendingPhone('')

        const identifiedData: PatientDiscoveryResult = {
          discoveryHandle: result.discovery_handle,
          expiresAt: result.expires_at,
          displayIdentifier: display.displayIdentifier,
          source: display.source,
        }

        setDiscoverySelection({
          discoveryHandle: result.discovery_handle,
          expiresAt: result.expires_at,
          displayIdentifier: display.displayIdentifier,
          source: display.source,
        })

        setLocalIdentified(identifiedData)
        if (onIdentified) {
          onIdentified(identifiedData)
        }
      } catch (caught: unknown) {
        if (
          caught instanceof ApiError &&
          (caught.status === 428 || caught.code === 'DISCOVERY_RECENT_MFA_REQUIRED')
        ) {
          setNeedsStepUp(true)
          setPendingPhone(input)
          setError(null)
        } else {
          setError(mapDiscoveryError(caught))
        }
      } finally {
        setLoading(false)
      }
    },
    [onIdentified, session?.hospital.hospital_id, setDiscoverySelection]
  )

  const handleResolve = () => {
    void executeDiscovery(value, mode)
  }

  const handleStepUp = async () => {
    const code = stepUpCode.trim()
    if (!/^\d{6}$/.test(code)) {
      setError('Enter a valid 6-digit authenticator code.')
      return
    }
    setStepUpLoading(true)
    setError(null)
    try {
      await NexaApiClient.verifyActionMfa(code)
      setNeedsStepUp(false)
      setStepUpCode('')
      // Automatically retry original phone search once without retyping
      if (pendingPhone) {
        void executeDiscovery(pendingPhone, 'phone')
      }
    } catch {
      setError('Authenticator code verification failed. Check the code and try again.')
    } finally {
      setStepUpLoading(false)
    }
  }

  const handleReset = () => {
    setLocalIdentified(null)
    setValue('')
    setError(null)
    setNeedsStepUp(false)
    if (onReset) onReset()
  }

  const handleContextualContinue = () => {
    if (!identified) return
    if (context === 'treatment') {
      router.push('/doctor/treatment-vitals')
    } else if (context === 'document') {
      router.push('/doctor/pipeline/upload')
    } else if (context === 'emergency') {
      router.push('/doctor/emergency-access')
    } else {
      router.push('/doctor/request-consent')
    }
  }

  const ctaLabel =
    context === 'document'
      ? 'Continue to Add Record'
      : context === 'treatment'
        ? 'Continue to Treatment Request'
        : context === 'emergency'
          ? 'Continue to Emergency Access'
          : 'Request Patient Access'

  // If patient already identified, render the clean success card
  if (identified) {
    return (
      <Surface padding="$5" gap="$4" borderRadius={14} backgroundColor="$nexaSurface" borderColor="$nexaSuccess">
        <XStack alignItems="center" justifyContent="space-between" flexWrap="wrap" gap="$3">
          <XStack alignItems="center" gap="$3">
            <YStack padding="$2" borderRadius={24} backgroundColor="$nexaSuccessSoft">
              <CheckCircle size={28} color="$nexaSuccess" />
            </YStack>
            <YStack gap="$1">
              <Text color="$nexaSuccess" fontWeight="800" fontSize={18}>
                ✓ Patient identified
              </Text>
              <Text color="$nexaSecondary" fontSize={14}>
                Identification method: {friendlySourceLabel(identified.source)}
              </Text>
            </YStack>
          </XStack>
          <ActionButton chromeless onPress={handleReset}>
            Change Patient
          </ActionButton>
        </XStack>

        <XStack justifyContent="flex-end" gap="$3" marginTop="$2">
          <ActionButton intent="primary" size="$4" onPress={handleContextualContinue}>
            <XStack alignItems="center" gap="$2">
              <Text color="$nexaOnAccent" fontWeight="700">
                {ctaLabel}
              </Text>
              <ArrowRight size={16} color="$nexaOnAccent" />
            </XStack>
          </ActionButton>
        </XStack>
      </Surface>
    )
  }

  return (
    <YStack gap="$4">
      {/* Clinician-friendly method tabs */}
      <XStack gap="$3" flexWrap="wrap">
        <ActionButton
          flex={1}
          minWidth={140}
          intent={mode === 'public_id' ? 'primary' : undefined}
          onPress={() => selectMode('public_id')}
        >
          <XStack alignItems="center" gap="$2">
            <Search size={18} color={mode === 'public_id' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'public_id' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              Nexa ID
            </Text>
          </XStack>
        </ActionButton>

        <ActionButton
          flex={1}
          minWidth={140}
          intent={mode === 'phone' ? 'primary' : undefined}
          onPress={() => selectMode('phone')}
        >
          <XStack alignItems="center" gap="$2">
            <Phone size={18} color={mode === 'phone' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'phone' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              Phone
            </Text>
          </XStack>
        </ActionButton>

        <ActionButton
          flex={1}
          minWidth={140}
          intent={mode === 'qr' ? 'primary' : undefined}
          onPress={() => selectMode('qr')}
        >
          <XStack alignItems="center" gap="$2">
            <QrCode size={18} color={mode === 'qr' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'qr' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              Scan QR
            </Text>
          </XStack>
        </ActionButton>

        <ActionButton
          flex={1}
          minWidth={140}
          intent={mode === 'nfc' ? 'primary' : undefined}
          onPress={() => selectMode('nfc')}
        >
          <XStack alignItems="center" gap="$2">
            <RadioReceiver size={18} color={mode === 'nfc' ? '$nexaOnAccent' : '$nexaSecondary'} />
            <Text color={mode === 'nfc' ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
              Scan NFC
            </Text>
          </XStack>
        </ActionButton>
      </XStack>

      <Surface padding="$5" gap="$4" borderRadius={14}>
        {/* Nexa ID Mode */}
        {mode === 'public_id' && (
          <YStack gap="$3">
            <SectionHeading>Find by Nexa ID</SectionHeading>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Enter the ID shown in the patient's Nexa Care app or health card.
            </Paragraph>
            <SearchInputField
              id="patient-search-id"
              label="Nexa Patient ID"
              placeholder="NC-..."
              value={value}
              onChangeText={(text) => {
                setValue(text.toUpperCase())
                if (error) setError(null)
              }}
              autoCapitalize="characters"
              autoCorrect={false}
              disabled={loading}
              onSubmitEditing={handleResolve}
            />
          </YStack>
        )}

        {/* Phone Mode */}
        {mode === 'phone' && (
          <YStack gap="$3">
            <SectionHeading>Find by phone</SectionHeading>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Enter the phone number registered with the patient's Nexa Care account.
            </Paragraph>
            <SearchInputField
              id="patient-search-phone"
              label="Patient phone number"
              placeholder="+91..."
              value={value}
              onChangeText={(text) => {
                setValue(text)
                if (error) setError(null)
              }}
              autoCapitalize="none"
              autoCorrect={false}
              disabled={loading || needsStepUp}
              onSubmitEditing={handleResolve}
            />

            {needsStepUp && (
              <Card
                padding="$4"
                gap="$3"
                borderRadius={12}
                borderWidth={1}
                borderColor="$nexaAccent"
                backgroundColor="$nexaAccentSoft"
              >
                <Text fontWeight="800" color="$nexaText" fontSize={15}>
                  Confirm your identity
                </Text>
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  For patient privacy, confirm your identity before searching by phone.
                </Paragraph>
                <SearchInputField
                  id="phone-step-up-code"
                  label="6-digit authenticator code"
                  placeholder="000000"
                  value={stepUpCode}
                  onChangeText={(code) => {
                    setStepUpCode(code)
                    if (error) setError(null)
                  }}
                  keyboardType="numeric"
                  maxLength={6}
                  secureTextEntry
                  disabled={stepUpLoading}
                  onSubmitEditing={handleStepUp}
                />
                <ActionButton
                  intent="primary"
                  disabled={stepUpLoading || stepUpCode.trim().length !== 6}
                  onPress={handleStepUp}
                >
                  {stepUpLoading ? <LoadingState label="Verifying identity..." /> : 'Confirm Identity'}
                </ActionButton>
              </Card>
            )}
          </YStack>
        )}

        {/* QR Mode */}
        {mode === 'qr' && (
          <YStack gap="$3">
            <SectionHeading>Scan Nexa QR</SectionHeading>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Scan the QR code shown in the patient's Nexa Care app or health card.
            </Paragraph>

            <YStack
              backgroundColor="$nexaAccentSoft"
              borderRadius={12}
              padding="$4"
              alignItems="center"
              gap="$2"
              borderWidth={1}
              borderColor="$nexaAccent"
            >
              <QrCode size={40} color="$nexaAccent" />
              <Text color="$nexaAccent" fontWeight="800" fontSize={16}>
                Position QR code in camera view
              </Text>
              <ActionButton
                chromeless
                onPress={() => setShowManualQr((prev) => !prev)}
              >
                {showManualQr ? 'Hide manual QR entry' : 'Enter QR code manually'}
              </ActionButton>
            </YStack>

            {showManualQr && (
              <SearchInputField
                id="patient-search-qr"
                label="Nexa QR code"
                placeholder="nexa://patient-discovery/v1/NC-..."
                value={value}
                onChangeText={(text) => {
                  setValue(text)
                  if (error) setError(null)
                }}
                autoCapitalize="none"
                autoCorrect={false}
                disabled={loading}
                onSubmitEditing={handleResolve}
              />
            )}
          </YStack>
        )}

        {/* NFC Mode */}
        {mode === 'nfc' && (
          <YStack gap="$3">
            <SectionHeading>Tap Nexa Card</SectionHeading>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Hold the patient's Nexa card near the NFC reader.
            </Paragraph>

            <YStack
              backgroundColor="$nexaAccentSoft"
              borderRadius={12}
              padding="$4"
              alignItems="center"
              gap="$2"
              borderWidth={1}
              borderColor="$nexaAccent"
            >
              <RadioReceiver size={40} color="$nexaAccent" />
              <Text color="$nexaAccent" fontWeight="800" fontSize={16}>
                Ready to scan Nexa card
              </Text>
              <ActionButton
                chromeless
                onPress={() => setShowManualNfc((prev) => !prev)}
              >
                {showManualNfc ? 'Hide card UID' : 'Enter card UID manually'}
              </ActionButton>
            </YStack>

            {showManualNfc && (
              <SearchInputField
                id="patient-search-nfc"
                label="Card UID"
                placeholder="Enter card UID..."
                value={value}
                onChangeText={(text) => {
                  setValue(text)
                  if (error) setError(null)
                }}
                autoCapitalize="characters"
                autoCorrect={false}
                disabled={loading}
                onSubmitEditing={handleResolve}
              />
            )}
          </YStack>
        )}

        {error && <InlineNotice title={error} tone="danger" />}

        {!needsStepUp && (mode === 'public_id' || (mode === 'phone' && !needsStepUp) || (mode === 'qr' && showManualQr) || (mode === 'nfc' && showManualNfc)) && (
          <XStack justifyContent="flex-end" gap="$3">
            <ActionButton
              intent="primary"
              disabled={loading || !value.trim()}
              aria-busy={loading}
              onPress={handleResolve}
            >
              {loading ? (
                <LoadingState label="Finding patient..." />
              ) : (
                <XStack alignItems="center" gap="$2">
                  <Text color="$nexaOnAccent" fontWeight="700">
                    Find Patient
                  </Text>
                  <ArrowRight size={16} color="$nexaOnAccent" />
                </XStack>
              )}
            </ActionButton>
          </XStack>
        )}
      </Surface>

      {/* Clinician-focused Privacy Banner */}
      {!compact && (
        <Surface backgroundColor="$nexaMuted" borderColor="$nexaBorder" padding="$4" borderRadius={12}>
          <XStack gap="$3" alignItems="center">
            <ShieldCheck size={22} color="$nexaAccent" />
            <YStack gap="$1" flex={1}>
              <Text color="$nexaText" fontWeight="700" fontSize={14}>
                Patient privacy protected
              </Text>
              <Paragraph color="$nexaSecondary" fontSize={13} lineHeight={20}>
                Medical information is shown only after the required patient access is approved.
              </Paragraph>
            </YStack>
          </XStack>
        </Surface>
      )}
    </YStack>
  )
}
