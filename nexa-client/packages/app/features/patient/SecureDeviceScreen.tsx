import { useRouter } from 'expo-router'
import { YStack, H2, Paragraph, Button, Spinner, Text, AnimatePresence } from 'tamagui'
import { useState } from 'react'
import {
  CurrentDeviceError,
  ensureCurrentDeviceEnrollment,
} from '../../services/currentDeviceEnrollment'
import { getRegisteredPushTokenForCurrentSession } from '../../services/pushNotifications'

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

export default function SecureDeviceScreen({ onEnrolled }: SecureDeviceScreenProps) {
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
      const enrollment = await ensureCurrentDeviceEnrollment({
        onStage: setStep,
        expoPushToken: getRegisteredPushTokenForCurrentSession(),
      })

      const deviceId = enrollment?.deviceId
      if (!deviceId) throw new Error('No device ID returned from enrollment')

      // Step 2: Navigate to enrolled confirmation
      onEnrolled?.(deviceId)
      router.replace({
        pathname: '/patient/enrolled',
        params: {
          deviceId,
          enrolledAt: new Date().toISOString(),
          deviceLabel: enrollment.status,
        },
      })
    } catch (err: unknown) {
      // Diagnostic is metadata-only: never log tokens, signatures, or key material.
      console.error('DEVICE_ENROLLMENT_ERROR', {
        code: err instanceof CurrentDeviceError ? err.code : 'UNKNOWN',
        status: err instanceof CurrentDeviceError ? err.status : 0,
      })
      const message =
        err instanceof Error
          ? err.message
          : typeof err === 'string'
            ? err
            : 'Unknown enrollment error'

      setError(`Device enrollment failed: ${message}`)
      setStep('ready')
    } finally {
      setLoading(false)
    }
  }

  return (
    <YStack
      flex={1}
      backgroundColor="$background"
      padding="$4"
      gap="$4"
      justifyContent="center"
      alignItems="center"
    >
      <YStack
        gap="$2"
        alignItems="center"
      >
        <Text fontSize={48}>🔐</Text>
        <H2
          color="$color"
          textAlign="center"
        >
          Secure This Device
        </H2>
        <Paragraph
          color="$color10"
          textAlign="center"
          size="$4"
          maxWidth={340}
        >
          To protect your health data, we&apos;ll link this device to your account using a
          cryptographic key stored in your phone&apos;s secure hardware.
        </Paragraph>
        <Paragraph
          color="$orange10"
          textAlign="center"
          size="$2"
          maxWidth={320}
        >
          ALPHA: P-256 keypair generated client-side and private key stored in platform secure
          storage. Not yet: hardware-backed non-exportable signing key with biometric-gated key
          usage.
        </Paragraph>
      </YStack>

      <YStack
        gap="$3"
        marginTop="$4"
        width="100%"
      >
        <AnimatePresence>
          {step === 'ready' && (
            <YStack
              gap="$3"
              transition="quick"
              enterStyle={{ opacity: 0, y: 10 }}
            >
              <Paragraph
                color="$color10"
                textAlign="center"
                size="$3"
              >
                What happens next:
              </Paragraph>
              <Paragraph
                color="$color10"
                textAlign="center"
                size="$3"
                opacity={0.8}
              >
                1. We generate a unique P-256 key on your device
              </Paragraph>
              <Paragraph
                color="$color10"
                textAlign="center"
                size="$3"
                opacity={0.8}
              >
                2. The private key stays in your phone&apos;s secure storage
              </Paragraph>
              <Paragraph
                color="$color10"
                textAlign="center"
                size="$3"
                opacity={0.8}
              >
                3. Only the public key is shared with Nexa Care
              </Paragraph>
              <Paragraph
                color="$color10"
                textAlign="center"
                size="$3"
                opacity={0.8}
              >
                4. You&apos;ll use biometrics to approve data access
              </Paragraph>
              <Button
                theme="blue"
                size="$4"
                disabled={loading}
                onPress={handleEnroll}
              >
                Secure This Device
              </Button>
            </YStack>
          )}

          {step === 'generating' && (
            <YStack
              gap="$2"
              alignItems="center"
              transition="quick"
              enterStyle={{ opacity: 0, y: 10 }}
            >
              <Spinner
                size="large"
                color="$blue10"
              />
              <Paragraph
                color="$color10"
                textAlign="center"
                size="$4"
              >
                Generating P-256 keypair in secure storage...
              </Paragraph>
            </YStack>
          )}

          {step === 'enrolling' && (
            <YStack
              gap="$2"
              alignItems="center"
              transition="quick"
              enterStyle={{ opacity: 0, y: 10 }}
            >
              <Spinner
                size="large"
                color="$blue10"
              />
              <Paragraph
                color="$color10"
                textAlign="center"
                size="$4"
              >
                Registering public key with Nexa Care...
              </Paragraph>
            </YStack>
          )}
        </AnimatePresence>
      </YStack>

      {error !== null ? (
        <Text
          color="$red10"
          textAlign="center"
          fontSize="$3"
        >
          {error}
        </Text>
      ) : null}
    </YStack>
  )
}
