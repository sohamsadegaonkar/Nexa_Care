'use client'

import { HomeScreen } from 'app/features/home/screen'
import { useRouter } from 'next/navigation'

export default function Page() {
  const router = useRouter()
  return (
    <HomeScreen
      onScannerPress={() => router.push('/scanner')}
      onEmergencyPress={() => router.push('/emergency')}
    />
  )
}
