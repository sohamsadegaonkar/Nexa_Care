import { useLocalSearchParams } from 'expo-router'
import PatientImportScreen from 'app/features/patient/PatientImportScreen'

export default function PatientRecordsImportExpoRoute() {
  const params = useLocalSearchParams<{ import_id?: string; returnTo?: string }>()
  return (
    <PatientImportScreen
      initialImportId={params.import_id}
      returnTo={params.returnTo}
    />
  )
}
