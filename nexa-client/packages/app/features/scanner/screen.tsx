'use client'

import { Button, Card, Spinner, Text, XStack, YStack } from '@my/ui'
import { RadioReceiver } from '@tamagui/lucide-icons'
import { useEffect, useState } from 'react'
import { Platform } from 'react-native'

import { WEB_MOCK_NFC_CARD_UID, useNfcScanner } from '../../hooks/useNfcScanner'

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

export function ScannerScreen() {
  const { status, patientId, errorMessage, isScanning, startScan } = useNfcScanner()
  const isWeb = Platform.OS === 'web'

  const handleWebSimulation = (): void => {
    void startScan(WEB_MOCK_NFC_CARD_UID)
  }

  const handleNativeScan = (): void => {
    void startScan()
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
    </YStack>
  )
}
