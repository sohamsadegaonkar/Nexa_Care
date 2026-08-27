'use client'

import { Button, Card, Paragraph, Spinner, Text, YStack } from '@my/ui'
import { RadioReceiver } from '@tamagui/lucide-icons'
import { useRouter } from 'solito/navigation'
import { Platform } from 'react-native'
import { WEB_MOCK_NFC_CARD_UID, useNfcScanner } from '../../hooks/useNfcScanner'
import { useProviderAuth } from '../doctor/ProviderAuthContext'

/** NFC starts the canonical discovery-bound consent workflow; no patient UUID is exposed. */
export function ScannerScreen() {
  const router = useRouter()
  const isWeb = Platform.OS === 'web'
  const { isAuthenticated, setDiscoverySelection } = useProviderAuth()
  const { status, discoveryHandle, expiresAt, errorMessage, isScanning, startScan } =
    useNfcScanner()
  const scan = async (cardUid?: string) => {
    const result = await startScan(cardUid)
    if (!result) return
    setDiscoverySelection({
      discoveryHandle: result.discovery_handle,
      expiresAt: result.expires_at,
      displayIdentifier: 'NFC card',
      source: 'nfc',
    })
    router.push('/doctor/request-consent')
  }
  if (!isAuthenticated)
    return (
      <YStack
        flex={1}
        alignItems="center"
        justifyContent="center"
        gap="$4"
      >
        <Text
          fontSize={22}
          fontWeight="900"
        >
          Session Required
        </Text>
        <Button onPress={() => router.push('/doctor/login')}>Go to Login</Button>
      </YStack>
    )
  return (
    <YStack
      flex={1}
      minHeight="100%"
      backgroundColor="$background"
      padding="$5"
      gap="$5"
      alignItems="center"
      justifyContent="center"
    >
      <YStack
        width="100%"
        maxWidth={520}
        gap="$5"
        alignItems="center"
      >
        <YStack
          gap="$3"
          alignItems="center"
        >
          {isScanning ? (
            <Spinner size="large" />
          ) : (
            <RadioReceiver
              size={92}
              color="$color12"
            />
          )}
          <Text
            fontSize={20}
            fontWeight="800"
          >
            NFC Reader
          </Text>
          <Paragraph textAlign="center">
            A successful scan starts a consent request. Patient identity is not shown before
            approval.
          </Paragraph>
        </YStack>
        {status === 'success' && discoveryHandle && expiresAt && (
          <Card
            width="100%"
            padding="$4"
          >
            <Text
              color="$green11"
              fontWeight="800"
            >
              NFC card verified. Opening consent request…
            </Text>
          </Card>
        )}
        {status === 'error' && errorMessage && (
          <Text
            color="$red11"
            fontWeight="800"
          >
            {errorMessage}
          </Text>
        )}
        <Button
          width="100%"
          maxWidth={320}
          size="$5"
          theme="blue"
          disabled={isScanning}
          onPress={() => void scan(isWeb ? WEB_MOCK_NFC_CARD_UID : undefined)}
        >
          {isScanning ? 'Scanning…' : isWeb ? 'Simulate NFC Tap' : 'Scan NFC Card'}
        </Button>
      </YStack>
    </YStack>
  )
}
