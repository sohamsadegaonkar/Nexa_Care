import { ProviderLoginScreen } from 'app/features/provider/ProviderLoginScreen'
import { Stack } from 'expo-router'

export default function Screen() {
  return (
    <>
      <Stack.Screen
        options={{
          title: 'Provider Login',
          headerShown: false,
        }}
      />
      <ProviderLoginScreen />
    </>
  )
}
