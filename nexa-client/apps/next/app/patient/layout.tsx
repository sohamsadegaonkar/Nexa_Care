import { PatientShell } from 'app/features/patient/PatientShell'

export default function PatientLayout({ children }: { children: React.ReactNode }) {
  return <PatientShell>{children}</PatientShell>
}
