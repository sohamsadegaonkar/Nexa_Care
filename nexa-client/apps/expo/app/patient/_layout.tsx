import { Stack, useRouter } from 'expo-router'
import { useTheme } from '@my/ui'
import { Pressable, Text } from 'react-native'

export const unstable_settings = {
  initialRouteName: 'access-history',
}

export default function PatientLayout() {
  const theme = useTheme()
  const router = useRouter()
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
        name="treatment-request"
        options={{ headerTitle: 'Treatment Session Request' }}
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
        options={{
          headerTitle: 'Access History',
          headerRight: () => (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Open phone discoverability settings"
              onPress={() => router.push('/patient/discoverability')}
              hitSlop={12}
            >
              <Text style={{ color: theme.nexaAccent.val, fontWeight: '600' }}>
                Privacy
              </Text>
            </Pressable>
          ),
        }}
      />
      <Stack.Screen
        name="timeline"
        options={{ headerTitle: 'Health Timeline' }}
      />
      <Stack.Screen
        name="records"
        options={{ headerTitle: 'Medical Records' }}
      />
      <Stack.Screen
        name="records/import"
        options={{ headerTitle: 'Import Record' }}
      />
      <Stack.Screen
        name="prescriptions"
        options={{ headerTitle: 'Prescriptions' }}
      />
      <Stack.Screen
        name="reports"
        options={{ headerTitle: 'Diagnostic Reports' }}
      />
      <Stack.Screen
        name="discoverability"
        options={{ headerTitle: 'Phone Discoverability' }}
      />
    </Stack>
  )
}
