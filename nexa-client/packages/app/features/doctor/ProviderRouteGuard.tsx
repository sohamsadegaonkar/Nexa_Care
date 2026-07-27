'use client'

import { Spinner, YStack } from '@my/ui'
import { useRouter } from 'next/navigation'
import { useEffect, type ReactNode } from 'react'
import { useProviderAuth } from './ProviderAuthContext'

interface ProviderRouteGuardProps {
  children: ReactNode
  returnTo: `/${string}`
}

export function ProviderRouteGuard({ children, returnTo }: ProviderRouteGuardProps) {
  const router = useRouter()
  const { hydrated, isAuthenticated } = useProviderAuth()

  useEffect(() => {
    if (hydrated && !isAuthenticated) {
      router.replace(`/doctor/login?returnTo=${encodeURIComponent(returnTo)}`)
    }
  }, [hydrated, isAuthenticated, returnTo, router])

  if (!hydrated || !isAuthenticated) {
    return (
      <YStack
        flex={1}
        bg="$background"
        justifyContent="center"
        alignItems="center"
      >
        <Spinner
          size="large"
          color="$blue10"
        />
      </YStack>
    )
  }

  return children
}
