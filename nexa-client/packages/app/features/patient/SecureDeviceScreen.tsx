import { useRouter } from 'expo-router'
import { AnimatePresence, Button, H2, Paragraph, Spinner, Text, YStack } from 'tamagui'
import { useState } from 'react'
import {
  CurrentDeviceError,
  ensureCurrentDeviceEnrollment,
} from '../../services/currentDeviceEnrollment'
import { getRegisteredPushTokenForCurrentSession } from '../../services/pushNotifications'

interface SecureDeviceScreenProps {
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
      const enrollment = await ensureCurrentDeviceEnrollment({
        onStage: setStep,
        expoPushToken: getRegisteredPushTokenForCurrentSession(),
      })
      onEnrolled?.(enrollment.deviceId)
      router.replace({
        pathname: '/patient/enrolled',
        params: { deviceId: enrollment.deviceId, enrolledAt: new Date().toISOString() },
      })
    } catch (err: unknown) {
      if (err instanceof CurrentDeviceError && err.code === 'RECOVERY_REQUIRED') {
        router.replace('/patient/recovery')
        return
      }
      console.error('DEVICE_ENROLLMENT_ERROR', {
        code: err instanceof CurrentDeviceError ? err.code : 'UNKNOWN',
        status: err instanceof CurrentDeviceError ? err.status : 0,
      })
      setError(err instanceof Error ? err.message : 'Device enrollment failed.')
      setStep('ready')
    } finally {
      setLoading(false)
    }
  }

  return (
    <YStack flex={1} backgroundColor="$background" padding="$4" gap="$4" justifyContent="center" alignItems="center">
      <YStack gap="$2" alignItems="center">
        <Text fontSize={48}>🔐</Text>
        <H2 textAlign="center">Secure This Device</H2>
        <Paragraph color="$color10" textAlign="center" maxWidth={350}>
          Nexa Care will create a P-256 signing key through this app&apos;s native device-security module and register only its public key with the server.
        </Paragraph>
        <Paragraph color="$orange10" textAlign="center" size="$2" maxWidth={350}>
          The app uses a native key handle for routine signing. CI can qualify native source compilation, but whether a particular Android key is hardware-backed or StrongBox-backed is determined by that device at runtime. Physical-device execution is qualified separately.
        </Paragraph>
      </YStack>

      <AnimatePresence>
        {step === 'ready' ? (
          <YStack gap="$3" width="100%" maxWidth={380}>
            <Paragraph color="$color10" textAlign="center">
              Only the public key and non-secret device metadata leave this installation. If this account already has device history, bootstrap enrollment is refused and recovery or trusted-device authorization is required.
            </Paragraph>
            <Button theme="blue" size="$4" disabled={loading} onPress={handleEnroll}>
              Secure This Device
            </Button>
          </YStack>
        ) : (
          <YStack gap="$2" alignItems="center">
            <Spinner size="large" />
            <Paragraph color="$color10">
              {step === 'generating' ? 'Creating native signing authority…' : 'Registering public key…'}
            </Paragraph>
          </YStack>
        )}
      </AnimatePresence>

      {error ? <Text color="$red10" textAlign="center">{error}</Text> : null}
    </YStack>
  )
}
