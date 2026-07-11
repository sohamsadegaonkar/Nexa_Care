'use client'

import { ProviderAuthProvider } from 'app/features/doctor/ProviderAuthContext'

export default function DoctorLayout({ children }: { children: React.ReactNode }) {
  return <ProviderAuthProvider>{children}</ProviderAuthProvider>
}
