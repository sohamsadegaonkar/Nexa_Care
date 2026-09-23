import { SearchScreen } from 'app/features/emergency/SearchScreen'
import { NativeProviderRouteGuard } from 'app/features/provider/NativeProviderRouteGuard'
import { Stack } from 'expo-router'

export default function Screen() {
  return (
    <>
      <Stack.Screen
        options={{
          title: 'Emergency',
          presentation: 'modal',
          animation: 'slide_from_right',
          gestureEnabled: true,
          gestureDirection: 'horizontal',
        }}
      />
      <NativeProviderRouteGuard>
        <SearchScreen />
      </NativeProviderRouteGuard>
    </>
  )
}
