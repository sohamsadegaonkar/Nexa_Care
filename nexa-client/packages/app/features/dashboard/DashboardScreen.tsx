'use client'

import { Card, Text, YStack, XStack, Button, Spinner } from '@my/ui'
import { TrendingUp, Users, Clock } from '@tamagui/lucide-icons'
import { useState, useEffect } from 'react'
import { NexaApiClient, ApiError } from '../../utils/apiClient'

interface DashboardMetrics {
  total_patients: number
  avg_appointment_duration: string
  revisit_rate: string
  productivity_score: number
}

export function DashboardScreen() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [timeRange, setTimeRange] = useState<'7d' | '30d' | '90d'>('30d')
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
        <Text color="$color11">Data-driven insights • Last updated just now</Text>
      </YStack>

      <XStack
        gap="$3"
        flexWrap="wrap"
      >
        {(['7d', '30d', '90d'] as const).map((range) => (
          <Button
            key={range}
            size="$3"
            theme={timeRange === range ? 'blue' : undefined}
            onPress={() => setTimeRange(range)}
          >
            {range}
          </Button>
        ))}
      </XStack>

      <YStack gap="$4">
        <XStack
          gap="$4"
          flexWrap="wrap"
        >
          <KpiCard
            icon={Users}
            label="Total Patients"
            value={metrics.total_patients.toLocaleString()}
            trend="+12%"
          />
          <KpiCard
            icon={Clock}
            label="Avg Duration"
            value={metrics.avg_appointment_duration}
            trend="-3m"
          />
          <KpiCard
            icon={TrendingUp}
            label="Revisit Rate"
            value={metrics.revisit_rate}
            trend="+4%"
          />
          <KpiCard
            icon={TrendingUp}
            label="Productivity"
            value={`${metrics.productivity_score}%`}
            trend="+8%"
          />
        </XStack>

        <Card
          p="$5"
          bg="$color2"
          borderWidth={1}
          borderColor="$borderColor"
        >
          <YStack gap="$4">
            <Text
              fontSize={18}
              fontWeight="900"
              color="$color12"
            >
              Productivity Trends
            </Text>
            <Text color="$color11">{timeRange} overview • Charts coming soon in v2</Text>
            <YStack
              gap="$3"
              pt="$2"
            >
              <MetricRow
                label="Appointments completed"
                value="312"
              />
              <MetricRow
                label="Avg patients per day"
                value="21.4"
              />
              <MetricRow
                label="Consent compliance rate"
                value="98.2%"
              />
            </YStack>
          </YStack>
        </Card>
      </YStack>
    </YStack>
  )
}

function KpiCard({
  icon: Icon,
  label,
  value,
  trend,
}: {
  icon: any
  label: string
  value: string
  trend: string
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
          <Text
            fontSize={12}
            color="$green10"
            fontWeight="700"
          >
            {trend}
          </Text>
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

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <XStack justify="space-between">
      <Text color="$color11">{label}</Text>
      <Text
        color="$color12"
        fontWeight="700"
      >
        {value}
      </Text>
    </XStack>
  )
}
