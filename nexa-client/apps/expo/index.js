import '@tamagui/native/setup-zeego'
import { registerRootComponent } from 'expo'
import { ExpoRoot } from 'expo-router'
import React from 'react'
import { configurePatientAuthTokenProvider } from 'app/services/deviceKeys'

// Restore the patient JWT provider after a native reload before protected
// patient routes make their first request.
configurePatientAuthTokenProvider()

// Must be exported or Fast Refresh won't update the context
export function App() {
  const ctx = require.context('./app')
  return <ExpoRoot context={ctx} />
}

registerRootComponent(App)
