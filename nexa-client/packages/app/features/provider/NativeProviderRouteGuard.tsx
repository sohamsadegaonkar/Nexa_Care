import { useRouter } from 'expo-router'
import { LoadingState, YStack } from '@my/ui'
import { useEffect, type ReactNode } from 'react'
import { useProviderAuthSession } from '../../services/providerAuthSession'

export interface NativeProviderRouteGuardProps {
  children: ReactNode
}

export function NativeProviderRouteGuard({ children }: NativeProviderRouteGuardProps) {
  const router = useRouter()
  const { hydrated, status } = useProviderAuthSession()

  useEffect(() => {
    if (hydrated && status !== 'authenticated') {
      router.replace('/provider/login')
    }
  }, [hydrated, status, router])

  if (!hydrated || status !== 'authenticated') {
    return (
      <YStack
        flex={1}
        bg="$background"
        justifyContent="center"
        alignItems="center"
        p="$4"
      >
        <LoadingState label="Verifying provider session..." />
      </YStack>
    )
  }

  return <>{children}</>
}
