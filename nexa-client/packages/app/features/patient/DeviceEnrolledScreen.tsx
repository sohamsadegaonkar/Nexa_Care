import { useRouter, useLocalSearchParams } from 'expo-router'
import { YStack, H2, Paragraph, Button, Text, Spinner } from 'tamagui'
import { useEffect, useState } from 'react'
import { getDevices, type DeviceInfo } from '../../services/deviceKeys'

/**
 * ALPHA: Device enrolled confirmation screen.
 *
 * Shows the enrollment success state, device label, and trusted status
 * fetched from the backend.
 *
 * Alpha: P-256 keypair generated client-side and private key stored in platform secure storage.
 * Not yet: hardware-backed non-exportable signing key with biometric-gated key usage.
 */

interface DeviceEnrolledScreenProps {
  /** Device ID returned from enrollment */
  deviceId?: string
  /** ISO 8601 timestamp of enrollment */
  enrolledAt?: string
}

interface TrustedDevice {
  device_id: string
  device_label: string | null
  platform: string
  status: string
  enrolled_at: string
}

export default function DeviceEnrolledScreen(_props?: DeviceEnrolledScreenProps) {
  const router = useRouter()
  const params = useLocalSearchParams<{ deviceId?: string; enrolledAt?: string }>()
  const deviceId = params.deviceId ?? ''
  const enrolledAt = params.enrolledAt ?? new Date().toISOString()

  const [countdown, setCountdown] = useState(5)
  const [deviceInfo, setDeviceInfo] = useState<TrustedDevice | null>(null)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [statusError, setStatusError] = useState<string | null>(null)

  // Fetch trusted device status from backend
  useEffect(() => {
    let cancelled = false

    async function fetchDeviceStatus() {
      setLoadingStatus(true)
      setStatusError(null)
      try {
        const response = await getDevices()
        if (cancelled) return
        // Find the matching device in the list
        const match = response.devices?.find((d: DeviceInfo) => d.device_id === deviceId)
        setDeviceInfo(match ?? response.devices?.[0] ?? null)
      } catch {
        if (!cancelled) {
          setStatusError('Could not verify device status.')
        }
      } finally {
        if (!cancelled) {
          setLoadingStatus(false)
        }
      }
    }

    if (deviceId) {
      fetchDeviceStatus()
    } else {
      setLoadingStatus(false)
    }

    return () => {
      cancelled = true
    }
  }, [deviceId])

  // Auto-navigate countdown
  useEffect(() => {
    if (countdown <= 0) {
      router.replace('/patient/access-history')
      return
    }
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000)
    return () => clearTimeout(timer)
  }, [countdown, router])

  const fingerprint =
    deviceId.length > 12 ? `${deviceId.slice(0, 6)}···${deviceId.slice(-6)}` : deviceId

  const deviceLabel = deviceInfo?.device_label ?? 'This Device'
  const deviceStatus = deviceInfo?.status ?? 'active'
  const isTrusted = deviceStatus === 'active'
  const platformLabel = deviceInfo?.platform ?? 'mobile'
  const enrolledDate = deviceInfo?.enrolled_at
    ? new Date(deviceInfo.enrolled_at).toLocaleDateString()
    : new Date(enrolledAt).toLocaleDateString()

  return (
    <YStack
      f={1}
      bg="$background"
      p="$4"
      gap="$4"
      jc="center"
      ai="center"
    >
      <Text fontSize={56}>✅</Text>

      <H2
        col="$color"
        ta="center"
      >
        Device Secured!
      </H2>

      <Paragraph
        col="$colorSubdued"
        ta="center"
        size="$5"
        mw={320}
      >
        Your device is now linked to your Nexa Care account. Only this device can approve data
        access requests.
      </Paragraph>

      <YStack
        bg="$backgroundHover"
        br="$4"
        p="$4"
        gap="$2"
        w="100%"
        mw={360}
      >
        <YStack
          fd="row"
          jc="space-between"
        >
          <Paragraph
            col="$colorSubdued"
            size="$3"
          >
            Device
          </Paragraph>
          <Text
            col="$color"
            size="$3"
            fontWeight="600"
          >
            {deviceLabel}
          </Text>
        </YStack>
        <YStack
          fd="row"
          jc="space-between"
        >
          <Paragraph
            col="$colorSubdued"
            size="$3"
          >
            Device ID
          </Paragraph>
          <Text
            col="$color"
            fontFamily="$mono"
            size="$3"
          >
            {fingerprint}
          </Text>
        </YStack>
        <YStack
          fd="row"
          jc="space-between"
        >
          <Paragraph
            col="$colorSubdued"
            size="$3"
          >
            Platform
          </Paragraph>
          <Text
            col="$color"
            size="$3"
          >
            {platformLabel}
          </Text>
        </YStack>
        <YStack
          fd="row"
          jc="space-between"
        >
          <Paragraph
            col="$colorSubdued"
            size="$3"
          >
            Enrolled
          </Paragraph>
          <Text
            col="$color"
            size="$3"
          >
            {enrolledDate}
          </Text>
        </YStack>
        <YStack
          fd="row"
          jc="space-between"
        >
          <Paragraph
            col="$colorSubdued"
            size="$3"
          >
            Status
          </Paragraph>
          {loadingStatus ? (
            <Spinner
              size="small"
              color="$blue10"
            />
          ) : statusError ? (
            <Text
              col="$orange10"
              size="$3"
            >
              {statusError}
            </Text>
          ) : (
            <Text
              col={isTrusted ? '$green10' : '$red10'}
              size="$3"
              fontWeight="700"
            >
              {isTrusted ? 'Trusted & Active' : deviceStatus}
            </Text>
          )}
        </YStack>
      </YStack>

      <Paragraph
        col="$orange10"
        ta="center"
        size="$2"
        mw={320}
      >
        ALPHA: P-256 keypair generated client-side and private key stored in platform secure
        storage. Not yet: hardware-backed non-exportable signing key with biometric-gated key usage.
      </Paragraph>

      <Button
        theme="blue"
        size="$4"
        mt="$2"
        onPress={() => router.replace('/patient/access-history')}
      >
        Continue to Dashboard ({countdown})
      </Button>
    </YStack>
  )
}
