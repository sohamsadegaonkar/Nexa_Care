import { Suspense } from 'react'
import { DoctorLoginScreen } from 'app/features/doctor/DoctorLoginScreen'

export default function Page() {
  return (
    <Suspense fallback={null}>
      <DoctorLoginScreen />
    </Suspense>
  )
}
