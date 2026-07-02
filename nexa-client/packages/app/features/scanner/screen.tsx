'use client'

import { Button, Card, Input, Sheet, Spinner, Text, XStack, YStack } from '@my/ui'
import { RadioReceiver } from '@tamagui/lucide-icons'
import { useRouter } from 'solito/navigation'
import { useEffect, useState } from 'react'
import { Platform } from 'react-native'

import { requestRoutineConsent, RoutineConsentError } from '../../api/consent'
import { WEB_MOCK_NFC_CARD_UID, useNfcScanner } from '../../hooks/useNfcScanner'

const DEFAULT_ACCESS_PURPOSE = 'ROUTINE_CHECKUP'

const PURPOSE_OPTIONS = [
  { label: 'Routine Checkup', value: 'ROUTINE_CHECKUP' },
  { label: 'Lab Review', value: 'LAB_REVIEW' },
]

function ScannerIcon({ pulsing }: { pulsing: boolean }) {
  const [pulseUp, setPulseUp] = useState(false)

  useEffect(() => {
    if (!pulsing) {
      setPulseUp(false)
      return undefined
    }

    const intervalId = setInterval(() => {
      setPulseUp((current) => !current)
    }, 850)

    return () => clearInterval(intervalId)
  }, [pulsing])

  return (
    <YStack
      scale={pulsing && pulseUp ? 1.08 : 1}
      opacity={pulsing && !pulseUp ? 0.82 : 1}
    >
      <RadioReceiver
        size={92}
        color="$color12"
        strokeWidth={2.5}
      />
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

function ConsentAccessSheet({
  patientId,
  open,
  selectedPurpose,
  loading,
  errorMessage,
  onOpenChange,
  onPurposeChange,
  onSubmit,
}: ConsentAccessSheetProps) {
  return (
    <Sheet
      modal
      open={open}
      onOpenChange={onOpenChange}
      snapPoints={[68]}
      dismissOnSnapToBottom={!loading}
    >
      <Sheet.Overlay
        bg="$shadow6"
        opacity={0.72}
      />
      <Sheet.Handle bg="$color8" />
      <Sheet.Frame
        bg="$background"
        p="$5"
        gap="$5"
      >
        <YStack gap="$2">
          <Text
            color="$color12"
            fontSize={24}
            fontWeight="900"
          >
            Patient Identity Verified
          </Text>
          <Text
            color="$color11"
            fontSize={16}
            fontWeight="700"
          >
            Declare purpose of access to generate 30-minute consent token.
          </Text>
        </YStack>

        {patientId && (
          <Card
            width="100%"
            borderWidth={2}
            borderColor="$green8"
            bg="$color2"
            p="$4"
          >
            <YStack gap="$1">
              <Text
                color="$green11"
                fontSize={14}
                fontWeight="900"
              >
                Verified Patient ID
              </Text>
              <Text
                color="$color12"
                fontSize={18}
                fontWeight="900"
              >
                {patientId}
              </Text>
            </YStack>
          </Card>
        )}

        <YStack gap="$3">
          <Text
            color="$color12"
            fontSize={15}
            fontWeight="900"
          >
            Purpose of Access
          </Text>
          <XStack
            gap="$2"
            flexWrap="wrap"
          >
            {PURPOSE_OPTIONS.map((option) => (
              <Button
                key={option.value}
                size="$4"
                theme={selectedPurpose === option.value ? 'blue' : undefined}
                disabled={loading}
                onPress={() => onPurposeChange(option.value)}
              >
                {option.label}
              </Button>
            ))}
          </XStack>
          <Input
            size="$5"
            value={selectedPurpose}
            disabled={loading}
            autoCapitalize="characters"
            onChangeText={onPurposeChange}
          />
        </YStack>

        {errorMessage && (
          <Text
            color="$red11"
            fontSize={16}
            fontWeight="900"
          >
            {errorMessage}
          </Text>
        )}

        <Button
          size="$5"
          theme="blue"
          disabled={loading || !patientId || selectedPurpose.trim().length === 0}
          onPress={onSubmit}
        >
          {loading ? (
            <XStack
              gap="$2"
              items="center"
            >
              <Spinner color="$color12" />
              <Text
                color="$color12"
                fontWeight="900"
              >
                Generating Token...
              </Text>
            </XStack>
          ) : (
            'Generate Token & View Record'
          )}
        </Button>
      </Sheet.Frame>
    </Sheet>
  )
}

export function ScannerScreen() {
  const { status, patientId, errorMessage, isScanning, startScan } = useNfcScanner()
  const router = useRouter()
  const isWeb = Platform.OS === 'web'
  const [showConsentModal, setShowConsentModal] = useState(false)
  const [selectedPurpose, setSelectedPurpose] = useState(DEFAULT_ACCESS_PURPOSE)
  const [isRequestingConsent, setIsRequestingConsent] = useState(false)
  const [consentErrorMessage, setConsentErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (status === 'success' && patientId) {
      setConsentErrorMessage(null)
      setShowConsentModal(true)
    }
  }, [status, patientId])

  const handleWebSimulation = (): void => {
    void startScan(WEB_MOCK_NFC_CARD_UID)
  }

  const handleNativeScan = (): void => {
    void startScan()
  }

  const handleGenerateConsent = async (): Promise<void> => {
    if (!patientId) {
      return
    }

    setIsRequestingConsent(true)
    setConsentErrorMessage(null)

    try {
      const purpose = selectedPurpose.trim()
      const consentToken = await requestRoutineConsent(patientId, purpose)
      setShowConsentModal(false)
      router.push(
        `/patient/${encodeURIComponent(patientId)}?consentToken=${encodeURIComponent(
          consentToken
        )}&purpose=${encodeURIComponent(purpose)}`
      )
    } catch (error: unknown) {
      if (error instanceof RoutineConsentError) {
        setConsentErrorMessage(error.message)
      } else {
        setConsentErrorMessage('Unable to generate consent token.')
      }
    } finally {
      setIsRequestingConsent(false)
    }
  }

  return (
    <YStack
      flex={1}
      minH="100%"
      bg="$background"
      p="$5"
      gap="$5"
      items="center"
      justify="center"
    >
      <YStack
        width="100%"
        maxW={520}
        gap="$5"
        items="center"
      >
        {status === 'idle' && (
          <YStack
            gap="$4"
            items="center"
          >
            {isWeb ? (
              <ScannerIcon pulsing={false} />
            ) : (
              <YStack
                gap="$3"
                items="center"
              >
                <Button
                  circular
                  chromeless
                  size="$10"
                  accessibilityLabel="Start NFC reader"
                  pressStyle={{ scale: 0.96 }}
                  onPress={handleNativeScan}
                >
                  <ScannerIcon pulsing />
                </Button>
                <Text
                  color="$color12"
                  fontSize={20}
                  fontWeight="800"
                  text="center"
                >
                  NFC Reader
                </Text>
              </YStack>
            )}
          </YStack>
        )}

        {status === 'scanning' && (
          <XStack
            gap="$3"
            items="center"
            justify="center"
          >
            <Spinner
              size="large"
              color="$blue11"
            />
            <Text
              color="$color12"
              fontSize={18}
              fontWeight="700"
              text="center"
            >
              Hold card to back of phone...
            </Text>
          </XStack>
        )}

        {status === 'success' && patientId && (
          <Card
            width="100%"
            borderWidth={2}
            p="$5"
            bg="$color2"
            borderColor="$green9"
          >
            <YStack gap="$2">
              <Text
                color="$green11"
                fontSize={15}
                fontWeight="800"
              >
                Resolved Patient ID
              </Text>
              <Text
                color="$color12"
                fontSize={24}
                fontWeight="900"
              >
                {patientId}
              </Text>
            </YStack>
          </Card>
        )}

        {status === 'error' && errorMessage && (
          <Text
            width="100%"
            color="$red11"
            fontSize={17}
            fontWeight="800"
            text="center"
          >
            {errorMessage}
          </Text>
        )}

        {isWeb ? (
          <Button
            width="100%"
            maxW={320}
            size="$5"
            theme="blue"
            disabled={isScanning}
            onPress={handleWebSimulation}
          >
            Simulate NFC Tap
          </Button>
        ) : (
          status !== 'idle' && (
            <Button
              width="100%"
              maxW={320}
              size="$5"
              theme="blue"
              disabled={isScanning}
              onPress={handleNativeScan}
            >
              NFC Reader
            </Button>
          )
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
        onSubmit={() => {
          void handleGenerateConsent()
        }}
      />
    </YStack>
  )
}
