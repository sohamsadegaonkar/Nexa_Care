import { ScannerScreen } from 'app/features/scanner/screen'
import { NativeProviderRouteGuard } from 'app/features/provider/NativeProviderRouteGuard'
import { ProviderAuthProvider } from 'app/features/doctor/ProviderAuthContext'
import { Stack } from 'expo-router'

export default function Screen() {
  return (
    <>
      <Stack.Screen
        options={{
          title: 'NFC Scanner',
          presentation: 'modal',
          animation: 'slide_from_right',
          gestureEnabled: true,
          gestureDirection: 'horizontal',
        }}
      />
      <NativeProviderRouteGuard>
        <ProviderAuthProvider>
          <ScannerScreen />
        </ProviderAuthProvider>
      </NativeProviderRouteGuard>
    </>
  )
}
