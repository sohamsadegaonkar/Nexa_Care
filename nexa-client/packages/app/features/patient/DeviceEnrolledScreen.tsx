import { useEffect, useState } from 'react'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { Button, H2, Paragraph, Spinner, Text, YStack } from 'tamagui'
import { getDevices, type DeviceInfo } from '../../services/deviceKeys'

export default function DeviceEnrolledScreen() {
  const router = useRouter()
  const params = useLocalSearchParams<{ deviceId?: string; enrolledAt?: string }>()
  const deviceId = params.deviceId ?? ''
  const enrolledAt = params.enrolledAt ?? new Date().toISOString()
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void getDevices()
      .then((response) => {
        if (cancelled) return
        setDeviceInfo(response.devices.find((device) => device.device_id === deviceId) ?? null)
      })
      .catch(() => {
        if (!cancelled) setError('Could not verify current device authority.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [deviceId])

  const active = deviceInfo?.status === 'active'
  return (
    <YStack flex={1} backgroundColor="$background" padding="$4" gap="$4" justifyContent="center" alignItems="center">
      <Text fontSize={54}>{active ? '✅' : '🔐'}</Text>
      <H2 textAlign="center">Device Authority</H2>
      {loading ? <Spinner size="large" /> : null}
      {error ? <Text color="$red10">{error}</Text> : null}
      {!loading && deviceInfo ? (
        <YStack width="100%" maxWidth={380} borderWidth={1} borderColor="$borderColor" borderRadius="$4" padding="$4" gap="$2">
          <Text fontWeight="700">{deviceInfo.device_label || 'This device'}</Text>
          <Paragraph color="$color10">Status: {deviceInfo.status}</Paragraph>
          <Paragraph color="$color10">Key version: {deviceInfo.key_version}</Paragraph>
          <Paragraph color="$color10">Platform: {deviceInfo.platform}</Paragraph>
          <Paragraph color="$color10">Enrolled: {new Date(deviceInfo.enrolled_at || enrolledAt).toLocaleString()}</Paragraph>
        </YStack>
      ) : null}
      <Paragraph color="$color10" textAlign="center" maxWidth={380}>
        Routine approvals use the native key alias associated with the active server key version. Native build qualification does not by itself prove physical Secure Enclave, Keystore, or StrongBox execution on this device.
      </Paragraph>
      <Button theme="blue" onPress={() => router.push('/patient/devices')}>Manage trusted devices</Button>
      <Button chromeless onPress={() => router.replace('/patient/access-history')}>Continue</Button>
    </YStack>
  )
}
