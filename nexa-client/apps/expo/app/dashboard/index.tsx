import { DashboardScreen } from 'app/features/dashboard/DashboardScreen'
import { NativeProviderRouteGuard } from 'app/features/provider/NativeProviderRouteGuard'
import { Stack } from 'expo-router'

export default function Screen() {
  return (
    <>
      <Stack.Screen
        options={{
          title: 'Dashboard',
          presentation: 'modal',
          animation: 'slide_from_right',
          gestureEnabled: true,
          gestureDirection: 'horizontal',
        }}
      />
      <NativeProviderRouteGuard>
        <DashboardScreen />
      </NativeProviderRouteGuard>
    </>
  )
}
