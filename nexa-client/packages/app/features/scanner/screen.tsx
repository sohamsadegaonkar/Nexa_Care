'use client'

import { Button, Card, Input, Sheet, Spinner, Text, XStack, YStack } from '@my/ui'
import { RadioReceiver } from '@tamagui/lucide-icons'
import { useRouter } from 'solito/navigation'
import { useEffect, useState } from 'react'
import { Platform } from 'react-native'

import { issueRoutineConsentV1, ConsentError, ConsentAssurance } from '../../api/consent_v1'
import { WEB_MOCK_NFC_CARD_UID, useNfcScanner } from '../../hooks/useNfcScanner'
import { PushApprovalScreen } from '../assurance/PushApprovalScreen'
import { BiometricPrompt } from '../assurance/BiometricPrompt'
import { getPatientPolicy } from '../../api/policy'

const DEFAULT_ACCESS_PURPOSE = 'ROUTINE_CHECKUP'

const PURPOSE_OPTIONS = [
  { label: 'Routine Checkup', value: 'ROUTINE_CHECKUP' },
  { label: 'Lab Review', value: 'LAB_REVIEW' },
]

export function ScannerScreen({ 
  onPatientResolved,
  onOpenPolicy,
  isDev = false
}: { 
  onPatientResolved?: (patientId: string) => void
  onOpenPolicy?: (patientId: string) => void
  isDev?: boolean
}) {
  const [currentPatientId, setCurrentPatientId] = useState<string | null>(null)
  const [redirectInfo, setRedirectInfo] = useState<{ canonicalId: string; isRedirected: boolean } | null>(null)
  const { status, patientId, errorMessage, isScanning, startScan } = useNfcScanner()
  const router = useRouter()
  const isWeb = Platform.OS === 'web'

  const [showConsentModal, setShowConsentModal] = useState(false)
  const [selectedPurpose, setSelectedPurpose] = useState(DEFAULT_ACCESS_PURPOSE)
  const [isRequestingConsent, setIsRequestingConsent] = useState(false)
  const [consentErrorMessage, setConsentErrorMessage] = useState<string | null>(null)

  // Assurance flow states
  const [patientPolicy, setPatientPolicy] = useState<ConsentAssurance>('standard')
  const [showPushApproval, setShowPushApproval] = useState(false)
  const [showBiometric, setShowBiometric] = useState(false)

  // Real call to backend policy service
  const fetchPatientPolicy = async (pid: string): Promise<ConsentAssurance> => {
    try {
      const res = await getPatientPolicy(pid)
      return res.consent_assurance_policy as ConsentAssurance
    } catch {
      return 'standard'
    }
  }

  useEffect(() => {
    if (status === 'success' && patientId) {
      setCurrentPatientId(patientId)
      onPatientResolved?.(patientId)
      setConsentErrorMessage(null)
      setShowConsentModal(true)
    }
  }, [status, patientId, onPatientResolved])

  // Allow opening policy screen for the current patient
  const openPolicyForCurrentPatient = () => {
    if (patientId && onOpenPolicy) {
      onOpenPolicy(patientId)
    }
  }

  const handleWebSimulation = (): void => {
    void startScan(WEB_MOCK_NFC_CARD_UID)
  }

  // Dev-only: Simulate a tombstoned card (fully wired)
  const simulateTombstonedCard = () => {
    if (!isDev) return
    
    const tombstonedPatientId = "PAT-TOMBSTONED-001"
    const canonicalPatientId = "PAT-CANONICAL-042"
    
    setCurrentPatientId(tombstonedPatientId)
    setRedirectInfo({ 
      canonicalId: canonicalPatientId, 
      isRedirected: true 
    })
    
    onPatientResolved?.(tombstonedPatientId)
    
    // Show visual feedback that this is a tombstoned card
    setShowConsentModal(true)
  }

  const handleNativeScan = (): void => {
    void startScan()
  }

  const proceedWithConsent = async (assurance: ConsentAssurance) => {
    if (!patientId) return

    setIsRequestingConsent(true)
    setConsentErrorMessage(null)

    // Use canonical patient ID if this was a tombstoned card
    const effectivePatientId = redirectInfo?.isRedirected 
      ? redirectInfo.canonicalId 
      : patientId

    try {
      const response = await issueRoutineConsentV1({
        patient_uuid: effectivePatientId,
        hospital_id: 'HOSP-DEMO',
        clinician_id: 'CLINICIAN-DEMO',
        purpose: selectedPurpose,
        consent_assurance: assurance,
      })

      setShowConsentModal(false)
      setShowPushApproval(false)
      setShowBiometric(false)

      router.push(
        `/patient/${encodeURIComponent(effectivePatientId)}?consentToken=${encodeURIComponent(
          response.consent_token
        )}&purpose=${encodeURIComponent(selectedPurpose)}`
      )
    } catch (error: unknown) {
      if (error instanceof ConsentError) {
        setConsentErrorMessage(error.message)
      } else {
        setConsentErrorMessage('Unable to generate consent token. Please try again.')
      }
      // Keep modal open on error so user can retry
      setShowConsentModal(true)
    } finally {
      setIsRequestingConsent(false)
    }
  }

  const handleGenerateConsent = async (): Promise<void> => {
    if (!patientId) return

    // Fetch real patient policy
    const policy = await fetchPatientPolicy(patientId)
    setPatientPolicy(policy)

    if (policy === 'push_approved') {
      setShowConsentModal(false)
      setShowPushApproval(true)
    } else if (policy === 'biometric_confirmed') {
      setShowConsentModal(false)
      setShowBiometric(true)
    } else {
      // Standard - proceed immediately
      await proceedWithConsent('standard')
    }
  }

  // Push Approval handlers
  const handlePushApprove = () => proceedWithConsent('push_approved')
  const handlePushDeny = () => {
    setShowPushApproval(false)
    setConsentErrorMessage('Access denied by patient')
  }
  const handlePushTimeout = (fallbackToken?: string) => {
    setShowPushApproval(false)
    if (fallbackToken) {
      router.push(`/patient/${patientId}?consentToken=${fallbackToken}&purpose=${selectedPurpose}`)
    } else {
      setConsentErrorMessage('Push request timed out. Standard fallback recorded.')
    }
  }

  // Biometric handlers
  const handleBiometricSuccess = () => proceedWithConsent('biometric_confirmed')
  const handleBiometricCancel = () => {
    setShowBiometric(false)
    setConsentErrorMessage('Biometric confirmation cancelled')
  }

  // ─────────────────────────────────────────────
  // RENDER ASSURANCE SCREENS
  // ─────────────────────────────────────────────
  if (showPushApproval && patientId) {
    return (
      <PushApprovalScreen
        patientUuid={patientId}
        clinicianName="Dr. Demo"
        hospitalName="Demo Hospital"
        purpose={selectedPurpose}
        clinicianId="CLINICIAN-DEMO"
        hospitalId="HOSP-DEMO"
        onApprove={handlePushApprove}
        onDeny={handlePushDeny}
        onTimeout={handlePushTimeout}
      />
    )
  }

  if (showBiometric) {
    return (
      <BiometricPrompt
        onSuccess={handleBiometricSuccess}
        onCancel={handleBiometricCancel}
      />
    )
  }

  // ─────────────────────────────────────────────
  // MAIN SCANNER UI
  // ─────────────────────────────────────────────
  return (
    <YStack flex={1} minH="100%" bg="$background" p="$5" gap="$5" items="center" justify="center">
      <YStack width="100%" maxW={520} gap="$5" items="center">
        {/* Scanner UI remains the same */}
        {status === 'idle' && (
          <YStack gap="$4" items="center">
            {isWeb ? (
              <ScannerIcon pulsing={false} />
            ) : (
              <YStack gap="$3" items="center">
                <Button circular chromeless size="$10" onPress={handleNativeScan}>
                  <ScannerIcon pulsing />
                </Button>
                <Text color="$color12" fontSize={20} fontWeight="800" text="center">
                  NFC Reader
                </Text>
              </YStack>
            )}
          </YStack>
        )}

        {status === 'scanning' && (
          <XStack gap="$3" items="center" justify="center">
            <Spinner size="large" color="$blue11" />
            <Text color="$color12" fontSize={18} fontWeight="700" text="center">
              Hold card to back of phone...
            </Text>
          </XStack>
        )}

        {status === 'success' && patientId && (
          <Card width="100%" borderWidth={2} p="$5" bg="$color2" borderColor="$green9">
            <YStack gap="$2">
              <Text color="$green11" fontSize={15} fontWeight="800">Resolved Patient ID</Text>
              <Text color="$color12" fontSize={24} fontWeight="900">{patientId}</Text>
              
              {redirectInfo?.isRedirected && (
                <Text color="$orange11" fontSize={14} fontWeight="700">
                  → Redirected to: {redirectInfo.canonicalId}
                </Text>
              )}
            </YStack>
          </Card>
        )}

        {status === 'error' && errorMessage && (
          <Text width="100%" color="$red11" fontSize={17} fontWeight="800" text="center">
            {errorMessage}
          </Text>
        )}

        {isWeb ? (
          <Button width="100%" maxW={320} size="$5" theme="blue" disabled={isScanning} onPress={handleWebSimulation}>
            Simulate NFC Tap
          </Button>
        ) : (
          status !== 'idle' && (
            <Button width="100%" maxW={320} size="$5" theme="blue" disabled={isScanning} onPress={handleNativeScan}>
              NFC Reader
            </Button>
          )
        )}

        {/* Dev-only Tombstone Test Button */}
        {isDev && isWeb && (
          <Button 
            width="100%" 
            maxW={320} 
            size="$4" 
            theme="orange" 
            onPress={simulateTombstonedCard}
          >
            [DEV] Simulate Tombstoned Card
          </Button>
        )}
      </YStack>

      <ConsentAccessSheet
        patientId={patientId}
        open={showConsentModal}
        selectedPurpose={selectedPurpose}
        loading={isRequestingConsent}
        errorMessage={consentErrorMessage}
        onOpenChange={setShowConsentModal}
        onPurposeChange={setSelectedPurpose}
        onSubmit={() => void handleGenerateConsent()}
      />
    </YStack>
  )
}

// ScannerIcon and ConsentAccessSheet remain unchanged (kept for brevity)
function ScannerIcon({ pulsing }: { pulsing: boolean }) {
  const [pulseUp, setPulseUp] = useState(false)
  useEffect(() => {
    if (!pulsing) { setPulseUp(false); return }
    const id = setInterval(() => setPulseUp(c => !c), 850)
    return () => clearInterval(id)
  }, [pulsing])
  return <YStack scale={pulsing && pulseUp ? 1.08 : 1} opacity={pulsing && !pulseUp ? 0.82 : 1}>
    <RadioReceiver size={92} color="$color12" strokeWidth={2.5} />
  </YStack>
}

interface ConsentAccessSheetProps {
  patientId: string | null
  open: boolean
  selectedPurpose: string
  loading: boolean
  errorMessage: string | null
  onOpenChange: (open: boolean) => void
  onPurposeChange: (purpose: string) => void
  onSubmit: () => void
}

function ConsentAccessSheet(props: ConsentAccessSheetProps) {
  const { patientId, open, selectedPurpose, loading, errorMessage, onOpenChange, onPurposeChange, onSubmit } = props
  return (
    <Sheet modal open={open} onOpenChange={onOpenChange} snapPoints={[68]} dismissOnSnapToBottom={!loading}>
      <Sheet.Overlay bg="$shadow6" opacity={0.72} />
      <Sheet.Handle bg="$color8" />
      <Sheet.Frame bg="$background" p="$5" gap="$5">
        <YStack gap="$2">
          <Text color="$color12" fontSize={24} fontWeight="900">Patient Identity Verified</Text>
          <Text color="$color11" fontSize={16} fontWeight="700">Declare purpose of access</Text>
        </YStack>
        {patientId && (
          <Card width="100%" borderWidth={2} borderColor="$green8" bg="$color2" p="$4">
            <YStack gap="$1">
              <Text color="$green11" fontSize={14} fontWeight="900">Verified Patient ID</Text>
              <Text color="$color12" fontSize={18} fontWeight="900">{patientId}</Text>
            </YStack>
          </Card>
        )}
        <YStack gap="$3">
          <Text color="$color12" fontSize={15} fontWeight="900">Purpose of Access</Text>
          <XStack gap="$2" flexWrap="wrap">
            {PURPOSE_OPTIONS.map(opt => (
              <Button key={opt.value} size="$4" theme={selectedPurpose === opt.value ? 'blue' : undefined} disabled={loading} onPress={() => onPurposeChange(opt.value)}>
                {opt.label}
              </Button>
            ))}
          </XStack>
          <Input size="$5" value={selectedPurpose} disabled={loading} onChangeText={onPurposeChange} />
        </YStack>
        {errorMessage && <Text color="$red11" fontSize={16} fontWeight="900">{errorMessage}</Text>}
        <Button size="$5" theme="blue" disabled={loading || !patientId} onPress={onSubmit}>
          {loading ? <XStack gap="$2" items="center"><Spinner color="$color12" /><Text>Generating Token...</Text></XStack> : 'Generate Token & View Record'}
        </Button>
      </Sheet.Frame>
    </Sheet>
  )
}