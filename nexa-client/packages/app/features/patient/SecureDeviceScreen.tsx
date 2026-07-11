import { useRouter } from 'expo-router'
import { YStack, H2, Paragraph, Button, Spinner, Text, AnimatePresence } from 'tamagui'
import { useState } from 'react'
import { generateAndEnrollDevice, setDeviceId } from '../../services/deviceKeys'

/**
 * ALPHA: Device key generation and enrollment screen.
 *
 * Alpha: P-256 keypair generated client-side and private key stored in platform secure storage.
 * Not yet: hardware-backed non-exportable signing key with biometric-gated key usage.
 *
 * For an academic/incubator demo, this is strong.  For hospital pilot
 * security, it still needs deeper native/hardware-backed key handling.
 */

interface SecureDeviceScreenProps {
  /** Phone number from login step */
  phoneNumber?: string
  /** Callback when enrollment succeeds */
  onEnrolled?: (deviceId: string) => void
}

export default function SecureDeviceScreen({
  onEnrolled,
}: SecureDeviceScreenProps) {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [step, setStep] = useState<'ready' | 'generating' | 'enrolling'>('ready')

  const handleEnroll = async () => {
    setLoading(true)
    setError(null)
    setStep('generating')

    try {
      // Step 1: Generate P-256 keypair + enroll with backend
      // Private key stays in SecureStore (Keychain / Keystore) — NEVER sent to server.
      setStep('generating')
      const enrollment = await generateAndEnrollDevice()

      const deviceId = enrollment?.device_id
      if (!deviceId) throw new Error('No device ID returned from enrollment')

      // Store device_id for later use in signed approval payloads
      await setDeviceId(deviceId)

      // Step 2: Navigate to enrolled confirmation
      onEnrolled?.(deviceId)
      router.replace({
        pathname: '/patient/enrolled',
        params: {
          deviceId,
          enrolledAt: enrollment.enrolled_at,
          deviceLabel: enrollment.status,
        },
      })
    } catch {
      setError('Device enrollment failed. Please try again.')
      setStep('ready')
    } finally {
      setLoading(false)
    }
  }

  return (
    <YStack f={1} bg="$background" p="$4" gap="$4" jc="center" ai="center">
      <YStack gap="$2" ai="center">
        <Text fontSize={48}>🔐</Text>
        <H2 col="$color" ta="center">
          Secure This Device
        </H2>
        <Paragraph col="$colorSubdued" ta="center" size="$4" mw={340}>
          To protect your health data, we&apos;ll link this device to your
          account using a cryptographic key stored in your phone&apos;s
          secure hardware.
        </Paragraph>
        <Paragraph col="$orange10" ta="center" size="$2" mw={320}>
          ALPHA: P-256 keypair generated client-side and private key stored
          in platform secure storage. Not yet: hardware-backed non-exportable
          signing key with biometric-gated key usage.
        </Paragraph>
      </YStack>

      <YStack gap="$3" mt="$4" w="100%">
        <AnimatePresence>
          {step === 'ready' && (
            <YStack gap="$3" animation="quick" enterStyle={{ o: 0, y: 10 }}>
              <Paragraph col="$colorSubdued" ta="center" size="$3">
                What happens next:
              </Paragraph>
              <Paragraph col="$colorSubdued" ta="center" size="$3" o={0.8}>
                1. We generate a unique P-256 key on your device
              </Paragraph>
              <Paragraph col="$colorSubdued" ta="center" size="$3" o={0.8}>
                2. The private key stays in your phone&apos;s secure storage
              </Paragraph>
              <Paragraph col="$colorSubdued" ta="center" size="$3" o={0.8}>
                3. Only the public key is shared with Nexa Care
              </Paragraph>
              <Paragraph col="$colorSubdued" ta="center" size="$3" o={0.8}>
                4. You&apos;ll use biometrics to approve data access
              </Paragraph>
              <Button theme="blue" size="$4" disabled={loading} onPress={handleEnroll}>
                Secure This Device
              </Button>
            </YStack>
          )}

          {step === 'generating' && (
            <YStack gap="$2" ai="center" animation="quick" enterStyle={{ o: 0, y: 10 }}>
              <Spinner size="large" color="$blue10" />
              <Paragraph col="$colorSubdued" ta="center" size="$4">
                Generating P-256 keypair in secure storage...
              </Paragraph>
            </YStack>
          )}

          {step === 'enrolling' && (
            <YStack gap="$2" ai="center" animation="quick" enterStyle={{ o: 0, y: 10 }}>
              <Spinner size="large" color="$blue10" />
              <Paragraph col="$colorSubdued" ta="center" size="$4">
                Registering public key with Nexa Care...
              </Paragraph>
            </YStack>
          )}
        </AnimatePresence>
      </YStack>

      {error && (
        <Text col="$red10" ta="center" size="$3">
          {error}
        </Text>
      )}
    </YStack>
  )
}
