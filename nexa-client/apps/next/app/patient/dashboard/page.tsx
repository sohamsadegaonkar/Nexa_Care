'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ActionButton,
  Paragraph,
  StatCard,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  FileText,
  FlaskConical,
  Lock,
  Pill,
  QrCode,
  ShieldCheck,
  UserCheck,
} from '@tamagui/lucide-icons'
import { usePatientAuthSession } from 'app/services/patientAuthSession'
import {
  apiClient,
  NexaApiClient,
  type PatientHealthSummaryResponse,
} from 'app/utils/apiClient'

interface AccessLogItem {
  audit_id: string
  doctor_name: string
  hospital_name: string
  purpose: string
  accessed_at: string
  is_break_glass: boolean
}

type LoadState = 'loading' | 'available' | 'unavailable'

export default function PatientDashboardPage() {
  const router = useRouter()
  const session = usePatientAuthSession()
  const [accessLogs, setAccessLogs] = useState<AccessLogItem[]>([])
  const [accessHistoryState, setAccessHistoryState] = useState<LoadState>('loading')
  const [timelineCount, setTimelineCount] = useState<number | null>(null)
  const [timelineState, setTimelineState] = useState<LoadState>('loading')
  const [publicPatientId, setPublicPatientId] = useState<string | null>(null)
  const [profileState, setProfileState] = useState<LoadState>('loading')
  const [healthSummary, setHealthSummary] = useState<PatientHealthSummaryResponse | null>(null)
  const [summaryState, setSummaryState] = useState<LoadState>('loading')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let mounted = true
    if (session.status !== 'authenticated') {
      setHealthSummary(null)
      setAccessLogs([])
      setTimelineCount(null)
      setPublicPatientId(null)
      const state: LoadState = session.status === 'hydrating' ? 'loading' : 'unavailable'
      setSummaryState(state)
      setAccessHistoryState(state)
      setTimelineState(state)
      setProfileState(state)
      return () => {
        mounted = false
      }
    }

    async function loadData() {
      setHealthSummary(null)
      setAccessLogs([])
      setTimelineCount(null)
      setPublicPatientId(null)
      setSummaryState('loading')
      setAccessHistoryState('loading')
      setTimelineState('loading')
      setProfileState('loading')

      // Fetch health summary
      try {
        const summaryData = await NexaApiClient.getMyHealthSummary()
        if (mounted) {
          setHealthSummary(summaryData)
          setSummaryState('available')
        }
      } catch {
        if (mounted) {
          setHealthSummary(null)
          setSummaryState('unavailable')
        }
      }

      // Fetch access history
      try {
        const historyRes = await apiClient.get<any>('/api/v2/patient/me/access-history?limit=3')
        const raw = (historyRes as any)?.data?.access_history ?? (historyRes as any)?.access_history
        if (mounted) {
          if (Array.isArray(raw)) {
            setAccessLogs(raw.slice(0, 3))
            setAccessHistoryState('available')
          } else {
            setAccessHistoryState('unavailable')
          }
        }
      } catch {
        if (mounted) setAccessHistoryState('unavailable')
      }

      // Fetch timeline count
      try {
        const timelineRes = await apiClient.get<any>('/api/v2/patient/me/timeline?limit=10')
        const rawEvents = (timelineRes as any)?.data?.events ?? (timelineRes as any)?.events
        if (mounted) {
          if (Array.isArray(rawEvents)) {
            setTimelineCount(rawEvents.length)
            setTimelineState('available')
          } else {
            setTimelineState('unavailable')
          }
        }
      } catch {
        if (mounted) setTimelineState('unavailable')
      }

      // Fetch profile
      try {
        const profRes = await apiClient.get<any>('/api/v2/patient/me/profile')
        const profData = (profRes as any)?.data || profRes
        if (mounted) {
          if (typeof profData?.public_patient_id === 'string' && profData.public_patient_id) {
            setPublicPatientId(profData.public_patient_id)
            setProfileState('available')
          } else {
            setProfileState('unavailable')
          }
        }
      } catch {
        if (mounted) setProfileState('unavailable')
      }
    }

    void loadData()
    return () => {
      mounted = false
    }
  }, [session.status, refreshKey])

  const displaySummaryValue = (value: string) => {
    if (summaryState === 'loading') return 'Loading…'
    if (summaryState === 'unavailable') return 'Unavailable'
    return value
  }

  const quickLinks = [
    {
      title: 'Timeline',
      description:
        timelineState === 'loading'
          ? 'Loading activity'
          : timelineState === 'unavailable'
            ? 'Activity unavailable'
            : timelineCount === 0
              ? 'No recent events'
              : `${timelineCount} recent events`,
      icon: Activity,
      route: '/patient/timeline',
    },
    {
      title: 'Records',
      description: 'Browse your health records',
      icon: FileText,
      route: '/patient/records',
    },
    {
      title: 'Prescriptions',
      description:
        summaryState === 'available'
          ? `${healthSummary?.counts.medications ?? 0} medication entries`
          : summaryState === 'loading'
            ? 'Loading record summary'
            : 'Record summary unavailable',
      icon: Pill,
      route: '/patient/prescriptions',
    },
    {
      title: 'Reports',
      description:
        summaryState === 'available'
          ? `${healthSummary?.counts.reports ?? 0} reports in your record`
          : summaryState === 'loading'
            ? 'Loading record summary'
            : 'Record summary unavailable',
      icon: FlaskConical,
      route: '/patient/reports',
    },
  ]

  return (
    <YStack gap="$6" maxWidth={1180} width="100%" marginHorizontal="auto">
      {/* Welcome & Patient Identity Banner */}
      <Surface padding="$5" borderRadius={16} elevation="$1">
        <XStack
          flexWrap="wrap"
          justifyContent="space-between"
          alignItems="center"
          gap="$4"
        >
          <YStack gap="$2" flex={1} minWidth={280}>
            <XStack alignItems="center" gap="$2.5" flexWrap="wrap">
              <StatusBadge tone={session.status === 'authenticated' ? 'success' : 'neutral'}>
                {session.status === 'authenticated'
                  ? 'Signed in'
                  : session.status === 'hydrating'
                    ? 'Checking session'
                    : 'Sign in required'}
              </StatusBadge>
              <ActionButton
                disabled={
                  session.status !== 'authenticated' ||
                  summaryState === 'loading' ||
                  accessHistoryState === 'loading' ||
                  timelineState === 'loading' ||
                  profileState === 'loading'
                }
                aria-busy={
                  summaryState === 'loading' ||
                  accessHistoryState === 'loading' ||
                  timelineState === 'loading' ||
                  profileState === 'loading'
                }
                onPress={() => setRefreshKey((key) => key + 1)}
              >
                Refresh
              </ActionButton>
            </XStack>
            <Text fontSize={28} fontWeight="800" color="$nexaText">
              Your health record
            </Text>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              See your records, prescriptions, and recent access in one place.
            </Paragraph>
          </YStack>

          {/* Quick Identity Chip */}
          <Surface
            padding="$3.5"
            borderRadius={12}
            backgroundColor="$nexaSurface"
            borderWidth={1}
            borderColor="$nexaBorder"
            minWidth={240}
          >
            <YStack gap="$2">
              <XStack alignItems="center" justifyContent="space-between">
                <Text fontSize={12} fontWeight="700" color="$nexaSecondary">
                  Patient identifier
                </Text>
                <QrCode size={16} color="$nexaAccent" />
              </XStack>
              <Text fontSize={15} fontWeight="800" color="$nexaText">
                {publicPatientId ||
                  (profileState === 'loading' ? 'Loading identifier…' : 'Identifier unavailable')}
              </Text>
              <XStack gap="$2" alignItems="center">
                <StatusBadge tone={session.status === 'authenticated' ? 'success' : 'neutral'}>
                  {session.status === 'authenticated'
                    ? 'Session active'
                    : session.status === 'hydrating'
                      ? 'Checking session'
                      : 'Not signed in'}
                </StatusBadge>
              </XStack>
            </YStack>
          </Surface>
        </XStack>
      </Surface>

      {/* Quick Longitudinal Hub Action Tiles */}
      <XStack flexWrap="wrap" gap="$3">
        {quickLinks.map(({ title, description, icon: Icon, route }) => (
          <ActionButton
            key={title}
            flex={1}
            minWidth={220}
            minHeight={104}
            height="auto"
            padding="$4"
            borderRadius={12}
            alignItems="stretch"
            justifyContent="center"
            aria-label={`Open ${title}`}
            onPress={() => router.push(route)}
          >
            <XStack width="100%" alignItems="center" justifyContent="space-between" gap="$3">
              <XStack flex={1} minWidth={0} alignItems="center" gap="$3">
                <YStack
                  width={40}
                  height={40}
                  flexShrink={0}
                  borderRadius={10}
                  backgroundColor="$nexaAccentSoft"
                  alignItems="center"
                  justifyContent="center"
                >
                  <Icon size={20} color="$nexaAccent" />
                </YStack>
                <YStack flex={1} minWidth={0} gap="$1">
                  <Text fontSize={16} fontWeight="700" color="$nexaText">
                    {title}
                  </Text>
                  <Paragraph fontSize={13} color="$nexaSecondary">
                    {description}
                  </Paragraph>
                </YStack>
              </XStack>
              <ArrowRight size={18} color="$nexaSecondary" />
            </XStack>
          </ActionButton>
        ))}
      </XStack>

      {/* Metrics Row */}
      <XStack flexWrap="wrap" gap="$4">
        <StatCard
          label="Active Medications"
          value={displaySummaryValue(
            healthSummary?.active_medications?.length
              ? `${healthSummary.active_medications.length} prescribed`
              : 'None recorded'
          )}
          tone="neutral"
          icon={<Pill size={20} />}
          description="Medication entries in your record"
        />
        <StatCard
          label="Allergies on File"
          value={displaySummaryValue(
            healthSummary?.allergy_highlights?.length
              ? `${healthSummary.allergy_highlights.length} recorded`
              : 'None on file'
          )}
          tone={healthSummary?.allergy_highlights?.length ? 'warning' : 'neutral'}
          icon={<AlertTriangle size={20} />}
          description="Entries documented in your record"
        />
        <StatCard
          label="Recent Labs"
          value={displaySummaryValue(
            healthSummary?.recent_labs?.length
              ? `${healthSummary.recent_labs.length} recent`
              : 'No recent results'
          )}
          tone="accent"
          icon={<FlaskConical size={20} />}
          description="Recent lab results in your record"
        />
        <StatCard
          label="Consent Gate"
          value="Enforced"
          tone="success"
          icon={<ShieldCheck size={20} />}
          description="Doctor access requires verified consent"
        />
      </XStack>

      {/* Main Grid: Clinical Highlights + Transparency */}
      <XStack
        flexWrap="wrap"
        gap="$5"
        flexDirection="column"
        $lg={{ flexDirection: 'row' }}
      >
        {/* Left Column: Personal Health Highlights */}
        <Surface
          flex={1}
          minWidth={320}
          padding="$5"
          borderRadius={16}
          elevation="$1"
        >
          <YStack gap="$4">
            <XStack justifyContent="space-between" alignItems="center">
              <YStack gap="$1">
                <Text fontSize={18} fontWeight="800" color="$nexaText">
                  Personal Clinical Highlights
                </Text>
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  Medication and allergy entries documented in your record
                </Paragraph>
              </YStack>
              <ActionButton
                onPress={() => router.push('/patient/records')}
              >
                <XStack alignItems="center" gap="$1.5">
                  <Text color="$nexaText" fontSize={13} fontWeight="600">All Records</Text>
                  <ArrowRight size={14} color="$nexaText" />
                </XStack>
              </ActionButton>
            </XStack>

            {/* Active Medications Preview */}
            <YStack gap="$2.5">
              <Text fontSize={14} fontWeight="700" color="$nexaText">
                Active medications
              </Text>
              {summaryState === 'loading' ? (
                <Paragraph fontSize={13} color="$nexaSecondary">
                  Loading medication information...
                </Paragraph>
              ) : summaryState === 'unavailable' ? (
                <Paragraph fontSize={13} color="$nexaSecondary">
                  Medication information is unavailable right now.
                </Paragraph>
              ) : healthSummary?.active_medications && healthSummary.active_medications.length > 0 ? (
                healthSummary.active_medications.slice(0, 3).map((m, idx) => (
                  <Surface
                    key={idx}
                    padding="$3"
                    borderRadius={10}
                    borderWidth={1}
                    borderColor="$nexaBorder"
                    backgroundColor="$nexaSurface"
                  >
                    <XStack justifyContent="space-between" alignItems="center">
                      <YStack gap="$0.5">
                        <Text fontSize={14} fontWeight="700" color="$nexaText">
                          {m.medication_name}
                        </Text>
                        <Paragraph fontSize={12} color="$nexaSecondary">
                          {m.dosage} • {m.frequency}
                        </Paragraph>
                      </YStack>
                      <StatusBadge tone="neutral">{m.source === 'manual' ? 'Prescribed' : 'Extracted'}</StatusBadge>
                    </XStack>
                  </Surface>
                ))
              ) : (
                <Paragraph fontSize={13} color="$nexaSecondary">
                  No active medication entries are recorded.
                </Paragraph>
              )}
            </YStack>

            {/* Allergies Highlight */}
            <YStack gap="$2.5">
              <XStack alignItems="center" gap="$2">
                <AlertTriangle size={16} color="$nexaWarning" />
                <Text fontSize={14} fontWeight="700" color="$nexaText">
                  Allergies and sensitivities
                </Text>
              </XStack>
              {summaryState === 'loading' ? (
                <Paragraph fontSize={13} color="$nexaSecondary">
                  Loading allergy information...
                </Paragraph>
              ) : summaryState === 'unavailable' ? (
                <Paragraph fontSize={13} color="$nexaSecondary">
                  Allergy information is unavailable right now.
                </Paragraph>
              ) : healthSummary?.allergy_highlights && healthSummary.allergy_highlights.length > 0 ? (
                healthSummary.allergy_highlights.slice(0, 3).map((a, idx) => (
                  <Surface
                    key={idx}
                    padding="$3"
                    borderRadius={10}
                    borderWidth={1}
                    borderColor="$nexaBorder"
                    backgroundColor="$nexaSurface"
                  >
                    <XStack justifyContent="space-between" alignItems="center">
                      <YStack gap="$0.5">
                        <Text fontSize={14} fontWeight="700" color="$nexaText">
                          {a.allergen}
                        </Text>
                        <Paragraph fontSize={12} color="$nexaSecondary">
                          Severity: {a.severity}
                        </Paragraph>
                      </YStack>
                      <StatusBadge tone="danger">{a.risk_level}</StatusBadge>
                    </XStack>
                  </Surface>
                ))
              ) : (
                <Paragraph fontSize={13} color="$nexaSecondary">
                  No allergy entries are recorded.
                </Paragraph>
              )}
            </YStack>

            <ActionButton
              intent="primary"
              onPress={() => router.push('/patient/timeline')}
            >
              <XStack alignItems="center" justifyContent="center" gap="$2">
                <Activity size={16} color="white" />
                <Text color="white" fontWeight="700" fontSize={14}>
                  Open Full Health Timeline
                </Text>
              </XStack>
            </ActionButton>
          </YStack>
        </Surface>

        {/* Right Column: Recent Access Ledger & Transparency */}
        <Surface
          flex={1}
          minWidth={320}
          padding="$5"
          borderRadius={16}
          elevation="$1"
        >
          <YStack gap="$4">
            <XStack justifyContent="space-between" alignItems="center">
              <YStack gap="$1">
                <Text fontSize={18} fontWeight="800" color="$nexaText">
                  Recent access
                </Text>
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  See which providers and facilities have accessed your records.
                </Paragraph>
              </YStack>
              <ActionButton
                onPress={() => router.push('/patient/access-history')}
              >
                <XStack alignItems="center" gap="$1.5">
                  <Text color="$nexaText" fontSize={13} fontWeight="600">View All</Text>
                  <ArrowRight size={14} color="$nexaText" />
                </XStack>
              </ActionButton>
            </XStack>

            {accessHistoryState === 'loading' ? (
              <Surface padding="$4" borderRadius={10} backgroundColor="$nexaMuted">
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  Checking recent access activity...
                </Paragraph>
              </Surface>
            ) : accessHistoryState === 'unavailable' ? (
              <Surface padding="$4" borderRadius={10} backgroundColor="$nexaMuted">
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  Recent access activity is unavailable right now.
                </Paragraph>
              </Surface>
            ) : accessLogs.length > 0 ? (
              <YStack gap="$3">
                {accessLogs.map((log, idx) => (
                  <Surface
                    key={log.audit_id || idx}
                    padding="$3.5"
                    borderRadius={10}
                    borderWidth={1}
                    borderColor="$nexaBorder"
                    backgroundColor="$nexaSurface"
                  >
                    <XStack justifyContent="space-between" alignItems="center" gap="$2">
                      <YStack gap="$1" flex={1}>
                        <XStack alignItems="center" gap="$2">
                          <UserCheck size={16} color="$nexaAccent" />
                          <Text fontSize={14} fontWeight="700" color="$nexaText">
                            {log.doctor_name || 'Clinician details unavailable'}
                          </Text>
                        </XStack>
                        <Paragraph color="$nexaSecondary" fontSize={12}>
                          {log.hospital_name || 'Facility unavailable'} • {log.purpose || 'Purpose unavailable'}
                        </Paragraph>
                      </YStack>
                      <StatusBadge tone={log.is_break_glass ? 'danger' : 'success'}>
                        {log.is_break_glass ? 'Emergency' : 'Routine'}
                      </StatusBadge>
                    </XStack>
                  </Surface>
                ))}
              </YStack>
            ) : (
              <Surface
                padding="$4"
                borderRadius={10}
                backgroundColor="$nexaMuted"
                alignItems="center"
                justifyContent="center"
              >
                <YStack alignItems="center" gap="$2" paddingVertical="$3">
                  <ShieldCheck size={32} color="$nexaAccent" />
                  <Text fontSize={14} fontWeight="700" color="$nexaText">
                    No recent access activity
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={13} textAlign="center">
                    No access entries were returned for this view.
                  </Paragraph>
                </YStack>
              </Surface>
            )}

            <Surface
              padding="$3.5"
              borderRadius={10}
              backgroundColor="$nexaSurface"
              borderWidth={1}
              borderColor="$nexaBorder"
            >
              <XStack gap="$3" alignItems="flex-start">
                <Lock size={20} color="$nexaAccent" />
                <YStack gap="$1" flex={1}>
                  <Text fontSize={14} fontWeight="700" color="$nexaText">
                    How access is controlled
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Routine access requires your approval. Emergency access follows a separate, time-limited process and is recorded in your access history.
                  </Paragraph>
                </YStack>
              </XStack>
            </Surface>
          </YStack>
        </Surface>
      </XStack>
    </YStack>
  )
}
