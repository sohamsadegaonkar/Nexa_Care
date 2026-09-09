import { Stack } from 'expo-router'
import { useTheme } from '@my/ui'

export const unstable_settings = {
  initialRouteName: 'access-history',
}

export default function PatientLayout() {
  const theme = useTheme()
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.nexaSurface.val },
        headerTintColor: theme.nexaText.val,
        headerTitleStyle: { fontWeight: '600' },
        contentStyle: {
          flex: 1,
          backgroundColor: theme.nexaCanvas.val,
        },
      }}
    >
      <Stack.Screen
        name="login"
        options={{ headerTitle: 'Nexa Care', headerShown: false }}
      />
      <Stack.Screen
        name="secure-device"
        options={{ headerTitle: 'Secure Your Device' }}
      />
      <Stack.Screen
        name="enrolled"
        options={{ headerTitle: 'Device Enrolled', headerBackVisible: false }}
      />
      <Stack.Screen
        name="consent-request"
        options={{ headerTitle: 'Consent Request' }}
      />
      <Stack.Screen
        name="biometric-approval"
        options={{ headerTitle: 'Verify Approval' }}
      />
      <Stack.Screen
        name="approval-result"
        options={{ headerTitle: 'Approval Result', headerBackVisible: false }}
      />
      <Stack.Screen
        name="access-history"
        options={{ headerTitle: 'Access History' }}
      />
      <Stack.Screen
        name="timeline"
        options={{ headerTitle: 'Health Timeline' }}
      />
    </Stack>
  )
}
