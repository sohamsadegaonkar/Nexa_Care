'use client'

import { ConsentHistoryScreen } from 'app/features/consent/ConsentHistoryScreen'
import { ProviderRouteGuard } from 'app/features/doctor/ProviderRouteGuard'

export default function ConsentHistoryPage() {
  return (
    <ProviderRouteGuard returnTo="/consent-history">
      <ConsentHistoryScreen />
    </ProviderRouteGuard>
  )
}
