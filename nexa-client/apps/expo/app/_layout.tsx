import { useCallback, useEffect } from 'react'
import { useColorScheme } from 'react-native'
import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native'
import { useFonts } from 'expo-font'
import { SplashScreen, Stack, useRouter } from 'expo-router'
import { Provider } from 'app/provider'
import {
  installConsentNotificationListeners,
  registerForPushNotifications,
} from 'app/services/pushNotifications'
import {
  hydratePatientAuthSession,
  usePatientAuthSession,
} from 'app/services/patientAuthSession'
import {
  CurrentDeviceError,
  ensureCurrentDeviceEnrollment,
} from 'app/services/currentDeviceEnrollment'

export const unstable_settings = {
  // Ensure that reloading on `/user` keeps a back button present.
  initialRouteName: 'Home',
}

// Prevent the splash screen from auto-hiding before asset loading is complete.
SplashScreen.preventAutoHideAsync()

export default function App() {
  const [interLoaded, interError] = useFonts({
    Inter: require('@tamagui/font-inter/otf/Inter-Medium.otf'),
    InterBold: require('@tamagui/font-inter/otf/Inter-Bold.otf'),
  })

  useEffect(() => {
    if (interLoaded || interError) {
      // Hide the splash screen after the fonts have loaded (or an error was returned) and the UI is ready.
      SplashScreen.hideAsync()
    }
  }, [interLoaded, interError])

  if (!interLoaded && !interError) {
    return null
  }

  return <RootLayoutNav />
}

function RootLayoutNav() {
  const colorScheme = useColorScheme()
  const router = useRouter()
  const patientAuth = usePatientAuthSession()
  const navigateToConsent = useCallback((requestId: string) => {
    router.push({
      pathname: '/patient/consent-request',
      params: { requestId },
    })
  }, [router])

  useEffect(() => {
    void hydratePatientAuthSession()
  }, [])

  useEffect(() => installConsentNotificationListeners(navigateToConsent), [navigateToConsent])

  useEffect(() => {
    if (!patientAuth.hydrated || patientAuth.status !== 'authenticated') return undefined
    const controller = new AbortController()
    void ensureCurrentDeviceEnrollment()
      .then(() => registerForPushNotifications({ signal: controller.signal }))
      .catch((error) => {
      if (controller.signal.aborted || process.env.NODE_ENV === 'production') return
      console.warn('PATIENT_DEVICE_SETUP_OR_PUSH_FAILED', {
        code: error instanceof CurrentDeviceError ? error.code : 'UNKNOWN',
        authState: patientAuth.status,
        hydrated: patientAuth.hydrated,
      })
      })
    return () => controller.abort()
  }, [patientAuth.hydrated, patientAuth.sessionKey, patientAuth.status])

  useEffect(() => {
    if (patientAuth.status === 'expired') router.replace('/patient/login')
  }, [patientAuth.status, router])

  return (
    <Provider>
      <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
        <Stack />
      </ThemeProvider>
    </Provider>
  )
}
