import { ProviderShell } from 'app/features/doctor/ProviderShell'

export default function DoctorLayout({ children }: { children: React.ReactNode }) {
  return <ProviderShell>{children}</ProviderShell>
}
