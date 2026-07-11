import { Stack } from 'expo-router'

export default function PatientLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: '#0A84FF' },
        headerTintColor: '#FFFFFF',
        headerTitleStyle: { fontWeight: '600' },
      }}
    >
      <Stack.Screen name="login" options={{ headerTitle: 'Nexa Care', headerShown: false }} />
      <Stack.Screen name="secure-device" options={{ headerTitle: 'Secure Your Device' }} />
      <Stack.Screen name="enrolled" options={{ headerTitle: 'Device Enrolled', headerBackVisible: false }} />
      <Stack.Screen name="consent-request" options={{ headerTitle: 'Consent Request' }} />
      <Stack.Screen name="biometric-approval" options={{ headerTitle: 'Verify Approval' }} />
      <Stack.Screen name="approval-result" options={{ headerTitle: 'Approval Result', headerBackVisible: false }} />
      <Stack.Screen name="access-history" options={{ headerTitle: 'Access History' }} />
      <Stack.Screen name="timeline" options={{ headerTitle: 'Health Timeline' }} />
    </Stack>
  )
}
