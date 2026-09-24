import PatientLoginScreen from 'app/features/patient/PatientLoginScreen'

export default function LoginRoute() {
  // Expo inlines direct EXPO_PUBLIC_* references at bundle time.  This merely
  // reveals a local synthetic-account affordance; the backend independently
  // rejects the route outside its explicit development-only boundary.
  const localDemoPatientLoginEnabled =
    process.env.EXPO_PUBLIC_APP_ENV === 'development' &&
    process.env.EXPO_PUBLIC_NEXA_DEMO_PATIENT_LOGIN === 'true'

  return <PatientLoginScreen localDemoPatientLoginEnabled={localDemoPatientLoginEnabled} />
}
