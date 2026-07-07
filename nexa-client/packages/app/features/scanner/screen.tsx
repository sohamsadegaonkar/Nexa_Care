'use client'

import { Button, Card, H2, H4, Input, Paragraph, Sheet, Spinner, Text, XStack, YStack } from '@my/ui'
import { RadioReceiver, AlertTriangle, XCircle, Clock, CheckCircle2 } from '@tamagui/lucide-icons'
import { useRouter } from 'solito/navigation'
import { useEffect, useState, useCallback, useRef } from 'react'
import { Platform } from 'react-native'

import { issueRoutineConsentV1, ConsentError, ConsentAssurance } from '../../api/consent_v1'
import { WEB_MOCK_NFC_CARD_UID, useNfcScanner } from '../../hooks/useNfcScanner'
import { getPatientPolicy } from '../../api/policy'
import { getPushRequestStatus, requestPushApproval } from '../../api/assurance'

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
  const { 
    status, 
    patientId, 
    canonicalPatientId, 
    isRedirected, 
    cardStatus, 
    errorMessage, 
    isScanning, 
    startScan 
  } = useNfcScanner()
  const router = useRouter()
  const isWeb = Platform.OS === 'web'
  const effectivePatientId = redirectInfo?.canonicalId ?? currentPatientId ?? patientId

  const [showConsentModal, setShowConsentModal] = useState(false)
  const [selectedPurpose, setSelectedPurpose] = useState(DEFAULT_ACCESS_PURPOSE)
  const [isRequestingConsent, setIsRequestingConsent] = useState(false)
  const [consentErrorMessage, setConsentErrorMessage] = useState<string | null>(null)

  const [showMergedBanner, setShowMergedBanner] = useState(true)
  const [showInactiveBanner, setShowInactiveBanner] = useState(true)

  // Assurance flow states
  const [patientPolicy, setPatientPolicy] = useState<ConsentAssurance>('standard')
  const [showBiometric, setShowBiometric] = useState(false)

  // Polling states
  const [pollingRequestId, setPollingRequestId] = useState<string | null>(null)
  const [pollStatus, setPollStatus] = useState<'pending' | 'approved' | 'denied' | 'expired' | 'error'>('pending')
  const [secondsLeft, setSecondsLeft] = useState(90)
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null)

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
      
      if (isRedirected && canonicalPatientId) {
        setRedirectInfo({ canonicalId: canonicalPatientId, isRedirected: true })
      } else {
        setRedirectInfo(null)
      }

      setConsentErrorMessage(null)
      setShowMergedBanner(true)
      setShowInactiveBanner(true)

      // Only proceed to consent if card is active
      if (cardStatus === 'active') {
        onPatientResolved?.(patientId)
        setShowConsentModal(true)
      }
    }
  }, [status, patientId, canonicalPatientId, isRedirected, cardStatus, onPatientResolved])

  // Allow opening policy screen for the current patient
  const openPolicyForCurrentPatient = () => {
    if (effectivePatientId && onOpenPolicy) {
      onOpenPolicy(effectivePatientId)
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
    setShowMergedBanner(true)
    setShowConsentModal(true)
  }

  const handleNativeScan = (): void => {
    void startScan()
  }

  const proceedWithConsent = async (assurance: ConsentAssurance) => {
    if (!effectivePatientId) return

    setIsRequestingConsent(true)
    setConsentErrorMessage(null)

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
    if (!effectivePatientId) return

    // Fetch real patient policy
    const policy = await fetchPatientPolicy(effectivePatientId)
    setPatientPolicy(policy)

    if (policy === 'push_approved') {
      setShowConsentModal(false)
      // Initiate push and start polling
      try {
        // NOTE: field names must match PushRequestPayload in
        // app/api/v2/assurance_routes.py exactly (patient_id / provider_id /
        // purpose / scope) -- the previous version sent patient_uuid /
        // clinician_name / hospital_name, none of which exist on the
        // backend schema, so this call 422'd before the URL-prefix bug
        // even mattered. clinician_name/hospital_name are not sent by the
        // client at all -- the backend derives the display name for the
        // patient's notification from the authenticated provider's own
        // session (provider.provider.display_name), not from this payload.
        //
        // TODO: providerId below is a placeholder. This screen has no
        // provider-session context wired in yet (see utils/api.ts -- auth
        // is bearer-token-only, no decoded provider profile is available
        // here). Thread the real authenticated provider's id through
        // before this goes in front of a hospital; a wrong-but-well-formed
        // provider_id will pass validation without failing loudly.
        const providerId = 'PLACEHOLDER_PROVIDER_ID'
        const { request_id } = await requestPushApproval({
          patient_id: effectivePatientId,
          provider_id: providerId,
          purpose: selectedPurpose,
          scope: 'clinical.read',
        })
        setPollingRequestId(request_id)
        setPollStatus('pending')
        setSecondsLeft(90)
      } catch (err) {
        setConsentErrorMessage('Failed to initiate push request.')
        setShowConsentModal(true)
      }
    } else if (policy === 'biometric_confirmed') {
      setShowConsentModal(false)
      setShowBiometric(true)
    } else {
      // Standard - proceed immediately
      await proceedWithConsent('standard')
    }
  }

  // ─────────────────────────────────────────────
  // POLLING LOGIC
  // ─────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current)
      pollIntervalRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!pollingRequestId || pollStatus !== 'pending') {
      stopPolling()
      return
    }

    pollIntervalRef.current = setInterval(async () => {
      try {
        const data = await getPushRequestStatus(pollingRequestId)
        
        // Update countdown from server time if available, or locally
        setSecondsLeft(prev => (prev > 0 ? prev - 2 : 0))

        if (data.status !== 'pending') {
          setPollStatus(data.status === 'timeout' ? 'expired' : data.status)
          stopPolling()

          if (data.status === 'approved') {
            // Auto-proceed to consent issuance
            await proceedWithConsent('push_approved')
          }
        }
      } catch (err) {
        // Graceful error handling - don't stop polling yet, might be transient
        console.error('Polling error:', err)
      }
    }, 2000)

    return () => stopPolling()
  }, [pollingRequestId, pollStatus, stopPolling])

  // ─────────────────────────────────────────────
  // RENDER POLLING SCREEN
  // ─────────────────────────────────────────────
  if (pollingRequestId) {
    return (
      <DoctorWaitingScreen 
        requestId={pollingRequestId}
        status={pollStatus}
        secondsLeft={secondsLeft}
        onCancel={() => {
          setPollingRequestId(null)
          setShowConsentModal(true)
        }}
        onRetry={() => {
          setPollingRequestId(null)
          void handleGenerateConsent()
        }}
        onUseStandard={() => {
          setPollingRequestId(null)
          void proceedWithConsent('standard')
        }}
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
        {status === 'success' && patientId && isRedirected && canonicalPatientId && showMergedBanner && (
          <MergedPatientBanner 
            originalId={patientId} 
            canonicalId={canonicalPatientId} 
            onDismiss={() => setShowMergedBanner(false)} 
          />
        )}

        {status === 'success' && (cardStatus === 'inactive' || cardStatus === 'lost') && showInactiveBanner && (
          <InactiveCardBanner 
            status={cardStatus} 
            onDismiss={() => setShowInactiveBanner(false)} 
          />
        )}

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
              <Text color="$color12" fontSize={24} fontWeight="900">{effectivePatientId}</Text>
              
              {isRedirected && canonicalPatientId && (
                <Text color="$orange11" fontSize={14} fontWeight="700">
                  → (Scanned: {patientId})
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
        patientId={effectivePatientId}
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

function MergedPatientBanner({ 
  originalId, 
  canonicalId, 
  onDismiss 
}: { 
  originalId: string; 
  canonicalId: string; 
  onDismiss: () => void 
}) {
  return (
    <YStack 
      width="100%"
      borderWidth={2} 
      borderColor="$yellow8" 
      backgroundColor="$yellow3" 
      borderRadius="$4" 
      padding="$3"
      gap="$2"
      onPress={onDismiss}
    >
      <XStack items="center" gap="$2">
        <AlertTriangle color="$yellow10" size={20} />
        <H4 color="$yellow10" size="$4">Record Merged</H4>
      </XStack>
      <Paragraph color="$yellow11" size="$3">
        ⚠️ This patient record has been merged. Displaying canonical record.
      </Paragraph>
      <XStack gap="$2" items="center">
        <Text fontWeight="700" size="$2" color="$yellow11">Original ID: {originalId}</Text>
        <Text size="$2" color="$yellow11">→</Text>
        <Text fontWeight="700" size="$2" color="$yellow11">Canonical ID: {canonicalId}</Text>
      </XStack>
    </YStack>
  )
}

function InactiveCardBanner({ 
  status, 
  onDismiss 
}: { 
  status: 'inactive' | 'lost'; 
  onDismiss: () => void 
}) {
  return (
    <YStack 
      width="100%"
      borderWidth={2} 
      borderColor="$red8" 
      backgroundColor="$red3" 
      borderRadius="$4" 
      padding="$3"
      gap="$2"
      onPress={onDismiss}
    >
      <XStack items="center" gap="$2">
        <XCircle color="$red10" size={20} />
        <H4 color="$red10" size="$4">Card {status === 'inactive' ? 'Inactive' : 'Lost'}</H4>
      </XStack>
      <Paragraph color="$red11" size="$3">
        🚫 This card has been reported as {status}. Please contact the administrator.
      </Paragraph>
    </YStack>
  )
}

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

interface DoctorWaitingScreenProps {
  requestId: string
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'error'
  secondsLeft: number
  onCancel: () => void
  onRetry: () => void
  onUseStandard: () => void
}

function DoctorWaitingScreen(props: DoctorWaitingScreenProps) {
  const { requestId, status, secondsLeft, onCancel, onRetry, onUseStandard } = props

  return (
    <YStack flex={1} items="center" justify="center" p="$6" gap="$6" bg="$background">
      {status === 'pending' && (
        <>
          <H2 text="center">Waiting for Patient Approval</H2>
          <Paragraph text="center" color="$color11">
            A consent notification has been sent to the patient's device.
          </Paragraph>
          <Spinner size="large" color="$blue10" />
          
          <YStack items="center" gap="$1">
            <XStack items="center" gap="$2">
              <Clock size={16} color="$yellow10" />
              <Text fontWeight="800" color="$yellow10">{secondsLeft}s remaining</Text>
            </XStack>
            <Text fontSize={12} color="$color10">Request ID: {requestId}</Text>
          </YStack>

          <Button mt="$4" chromeless onPress={onCancel}>Cancel Request</Button>
        </>
      )}

      {status === 'approved' && (
        <>
          <CheckCircle2 size={64} color="$green10" />
          <H2 color="$green10">Request Approved</H2>
          <Paragraph>Preparing record for viewing...</Paragraph>
          <Spinner color="$green10" />
        </>
      )}

      {status === 'denied' && (
        <>
          <XCircle size={64} color="$red10" />
          <H2 color="$red10">Request Denied</H2>
          <Paragraph text="center">Patient denied the consent request.</Paragraph>
          <XStack gap="$3" mt="$4">
            <Button theme="blue" onPress={onRetry}>Try Again</Button>
            <Button chromeless onPress={onUseStandard}>Use Standard Consent</Button>
          </XStack>
        </>
      )}

      {status === 'expired' && (
        <>
          <Clock size={64} color="$yellow10" />
          <H2 color="$yellow10">Request Timed Out</H2>
          <Paragraph text="center">Patient did not respond in time.</Paragraph>
          <XStack gap="$3" mt="$4">
            <Button theme="blue" onPress={onRetry}>Retry</Button>
            <Button variant="outlined" onPress={onUseStandard}>Use Standard Consent</Button>
          </XStack>
        </>
      )}

      {status === 'error' && (
        <>
          <AlertTriangle size={64} color="$red10" />
          <H2 color="$red10">System Error</H2>
          <Paragraph>Unable to verify approval status.</Paragraph>
          <Button mt="$4" theme="blue" onPress={onRetry}>Retry</Button>
        </>
      )}
    </YStack>
  )
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