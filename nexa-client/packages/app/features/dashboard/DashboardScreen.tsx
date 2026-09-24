'use client'

import { Card, Text, YStack, XStack, Button, Spinner, Separator } from '@my/ui'
import { Users, ShieldCheck, ShieldAlert, Clock } from '@tamagui/lucide-icons'
import { useState, useEffect } from 'react'
import { useRouter } from 'solito/navigation'
import { NexaApiClient, ApiError, type DashboardMetrics } from '../../utils/apiClient'
import { clearProviderAuthSession } from '../../services/providerAuthSession'

export function DashboardScreen() {
  const router = useRouter()
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let isMounted = true

    const fetchMetrics = async () => {
      setLoading(true)
      setError(null)

      try {
        const data = await NexaApiClient.getDashboardMetrics()

        if (isMounted) {
          setMetrics(data)
        }
      } catch (error: unknown) {
        if (!isMounted) {
          return
        }

        setMetrics(null)

        if (error instanceof ApiError) {
          if (error.status === 401) {
            setError('Your provider session has expired. Sign in again to view dashboard metrics.')
            return
          }

          if (error.status === 403) {
            setError('Your provider role is not permitted to view dashboard metrics.')
            return
          }
        }

        setError('Dashboard metrics are temporarily unavailable.')
      } finally {
        if (isMounted) {
          setLoading(false)
        }
      }
    }

    fetchMetrics()

    return () => {
      isMounted = false
    }
  }, [refreshKey])

  if (loading) {
    return (
      <YStack
        flex={1}
        items="center"
        justify="center"
        bg="$background"
        gap="$3"
      >
        <Spinner
          size="large"
          color="$blue10"
        />
        <Text color="$color11">Loading dashboard...</Text>
      </YStack>
    )
  }

  if (error || !metrics) {
    return (
      <YStack
        flex={1}
        items="center"
        justify="center"
        bg="$background"
        p="$5"
      >
        <Card
          width="100%"
          maxW={460}
          p="$5"
          bg="$color2"
          borderWidth={1}
          borderColor="$borderColor"
        >
          <YStack gap="$3">
            <Text
              fontSize={18}
              fontWeight="900"
              color="$color12"
            >
              Dashboard unavailable
            </Text>
            <Text color="$red11">{error ?? 'Unable to load dashboard metrics.'}</Text>
            <Button
              theme="blue"
              onPress={() => setRefreshKey((current) => current + 1)}
            >
              Retry
            </Button>
          </YStack>
        </Card>
      </YStack>
    )
  }

  return (
    <YStack
      flex={1}
      bg="$background"
      p="$5"
      gap="$6"
    >
      <YStack gap="$2">
        <Text
          fontSize={28}
          fontWeight="900"
          color="$color12"
        >
          Provider Dashboard
        </Text>
        <Text color="$color11">Metrics returned by your care organization</Text>
      </YStack>

      <YStack gap="$4">
        <XStack
          gap="$4"
          flexWrap="wrap"
        >
          <KpiCard
            icon={Users}
            label="Total Patients"
            value={metrics.total_patients.toLocaleString()}
          />
          <KpiCard
            icon={ShieldCheck}
            label="Active Consents"
            value={metrics.active_consents.toLocaleString()}
          />
          <KpiCard
            icon={ShieldAlert}
            label="Break-Glass Grants"
            value={metrics.break_glass_grants.toLocaleString()}
          />
          <KpiCard
            icon={Clock}
            label="Review Backlog"
            value={metrics.review_backlog.toLocaleString()}
          />
        </XStack>
      </YStack>

      <Separator borderColor="$borderColor" />

      <YStack gap="$3">
        <Text
          fontSize={16}
          fontWeight="700"
          color="$color12"
        >
          Clinical Workflows
        </Text>
        <XStack
          gap="$3"
          flexWrap="wrap"
        >
          <Button
            theme="blue"
            onPress={() => router.push('/scanner')}
          >
            NFC Scanner
          </Button>
          <Button
            theme="red"
            onPress={() => router.push('/emergency')}
          >
            Emergency Break-Glass
          </Button>
          <Button
            onPress={async () => {
              try {
                await clearProviderAuthSession()
              } catch {
                // Ignore cleanup error
              }
              router.replace('/')
            }}
          >
            Sign Out
          </Button>
        </XStack>
      </YStack>
    </YStack>
  )
}

function KpiCard({
  icon: Icon,
  label,
  value,
}: {
  icon: any
  label: string
  value: string
}) {
  return (
    <Card
      flex={1}
      minWidth={140}
      p="$4"
      bg="$color2"
      borderWidth={1}
      borderColor="$borderColor"
    >
      <YStack gap="$2">
        <XStack
          justify="space-between"
          items="center"
        >
          <Icon
            size={22}
            color="$blue10"
          />
        </XStack>
        <Text
          fontSize={13}
          color="$color11"
          fontWeight="700"
        >
          {label}
        </Text>
        <Text
          fontSize={24}
          fontWeight="900"
          color="$color12"
        >
          {value}
        </Text>
      </YStack>
    </Card>
  )
}
