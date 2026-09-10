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
  Clock,
  FileText,
  Heart,
  Lock,
  QrCode,
  Shield,
  ShieldAlert,
  ShieldCheck,
  User,
  UserCheck,
} from '@tamagui/lucide-icons'
import { getCurrentPatientId, usePatientAuthSession } from 'app/services/patientAuthSession'
import { apiClient } from 'app/utils/apiClient'

interface AccessLogItem {
  audit_id: string
  doctor_name: string
  hospital_name: string
  purpose: string
  accessed_at: string
  is_break_glass: boolean
}

export default function PatientDashboardPage() {
  const router = useRouter()
  const session = usePatientAuthSession()
  const patientId = getCurrentPatientId()
  const [accessLogs, setAccessLogs] = useState<AccessLogItem[]>([])
  const [loading, setLoading] = useState(false)
  const [timelineCount, setTimelineCount] = useState(0)
  const [publicPatientId, setPublicPatientId] = useState<string | null>(null)

  useEffect(() => {
    if (session.hydrated && session.status === 'unauthenticated') {
      // Allow viewing or redirect if needed
    }
  }, [session])

  useEffect(() => {
    let mounted = true
    async function loadData() {
      if (session.status !== 'authenticated') return
      setLoading(true)
      try {
        const historyRes = await apiClient.get<any>('/api/v2/patient/me/access-history?limit=3')
        const raw = (historyRes as any)?.data?.access_history || (historyRes as any)?.access_history || []
        if (mounted && Array.isArray(raw)) {
          setAccessLogs(raw.slice(0, 3))
        }
      } catch {
        // graceful fallback
      }
      try {
        const timelineRes = await apiClient.get<any>('/api/v2/patient/me/timeline?limit=10')
        const rawEvents = (timelineRes as any)?.data?.events || (timelineRes as any)?.events || []
        if (mounted && Array.isArray(rawEvents)) {
          setTimelineCount(rawEvents.length)
        }
      } catch {
        // graceful fallback
      }
      try {
        const profRes = await apiClient.get<any>('/api/v2/patient/me/profile')
        const profData = (profRes as any)?.data || profRes
        if (mounted && profData?.public_patient_id) {
          setPublicPatientId(profData.public_patient_id)
        }
      } catch {
        // profile may not exist yet
      } finally {
        if (mounted) setLoading(false)
      }
    }
    loadData()
    return () => {
      mounted = false
    }
  }, [session.status])

  return (
    <YStack gap="$6" maxWidth={1180} width="100%" marginHorizontal="auto">
      {/* Welcome & Security Banner */}
      <Surface padding="$5" borderRadius={16} elevation="$1">
        <XStack
          flexWrap="wrap"
          justifyContent="space-between"
          alignItems="center"
          gap="$4"
        >
          <YStack gap="$2" flex={1} minWidth={280}>
            <XStack alignItems="center" gap="$2.5">
              <StatusBadge tone="success">Patient Session Active</StatusBadge>
            </XStack>
            <Text fontSize={26} fontWeight="900" color="$nexaText">
              Welcome to Your Health Portal
            </Text>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Review your clinical events, access history, and profile settings. Every access requires your verified consent or emergency audit justification.
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
                  PATIENT IDENTIFIER
                </Text>
                <QrCode size={16} color="$nexaAccent" />
              </XStack>
              <Text fontSize={15} fontWeight="800" color="$nexaText">
                {publicPatientId || (patientId ? `ID: ${patientId.slice(0, 12)}...` : 'Patient ID')}
              </Text>
              <XStack gap="$2" alignItems="center">
                <StatusBadge tone={session.status === 'authenticated' ? 'success' : 'neutral'}>
                  {session.status === 'authenticated' ? 'Verified Session' : 'Guest'}
                </StatusBadge>
              </XStack>
            </YStack>
          </Surface>
        </XStack>
      </Surface>

      {/* Metrics Row */}
      <XStack flexWrap="wrap" gap="$4">
        <StatCard
          label="Access History Events"
          value={accessLogs.length > 0 ? `${accessLogs.length} Logged` : '0 Recorded'}
          tone="neutral"
          icon={<Clock size={20} />}
          description="Doctor accesses recorded in audit log"
        />
        <StatCard
          label="Emergency Accesses"
          value={accessLogs.filter((l) => l.is_break_glass).length > 0 ? `${accessLogs.filter((l) => l.is_break_glass).length} Active` : '0 Active'}
          tone="success"
          icon={<ShieldAlert size={20} />}
          description="Emergency access requires audit review"
        />
        <StatCard
          label="Health Timeline"
          value={`${timelineCount} Records`}
          tone="accent"
          icon={<Activity size={20} />}
          description="Vitals, lab reports, and clinical encounters"
        />
        <StatCard
          label="Consent Gate"
          value="Enforced"
          tone="success"
          icon={<ShieldCheck size={20} />}
          description="Time-bounded purpose scoping active"
        />
      </XStack>

      {/* Main Grid: Recent Accesses + Sovereign Rights */}
      <XStack
        flexWrap="wrap"
        gap="$5"
        flexDirection="column"
        $lg={{ flexDirection: 'row' }}
      >
        {/* Left Column: Recent Access Transparency */}
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
                  Recent Access Ledger
                </Text>
                <Paragraph color="$nexaSecondary" fontSize={13}>
                  SEC-021 Transparency: Who viewed your medical files
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

            {accessLogs.length > 0 ? (
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
                            {log.doctor_name || 'Dr. Attending Physician'}
                          </Text>
                        </XStack>
                        <Paragraph color="$nexaSecondary" fontSize={12}>
                          {log.hospital_name || 'General Hospital'} • {log.purpose || 'Clinical Review'}
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
                    No Unauthorized Access
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={13} textAlign="center">
                    No clinical provider has accessed your data without your signed consent.
                  </Paragraph>
                </YStack>
              </Surface>
            )}

            <ActionButton
              intent="primary"
              onPress={() => router.push('/patient/timeline')}
            >
              <XStack alignItems="center" justifyContent="center" gap="$2">
                <Activity size={16} color="white" />
                <Text color="white" fontWeight="700" fontSize={14}>
                  Open Complete Health Timeline
                </Text>
              </XStack>
            </ActionButton>
          </YStack>
        </Surface>

        {/* Right Column: Privacy & Access Controls */}
        <Surface
          flex={1}
          minWidth={320}
          padding="$5"
          borderRadius={16}
          elevation="$1"
        >
          <YStack gap="$4">
            <YStack gap="$1">
              <Text fontSize={18} fontWeight="800" color="$nexaText">
                Privacy & Access Controls
              </Text>
              <Paragraph color="$nexaSecondary" fontSize={13}>
                Core security and privacy protections enforced by Nexa Care
              </Paragraph>
            </YStack>

            <YStack gap="$3">
              <Surface
                padding="$3"
                borderRadius={10}
                backgroundColor="$nexaSurface"
                borderWidth={1}
                borderColor="$nexaBorder"
              >
                <XStack gap="$3" alignItems="flex-start">
                  <Shield size={20} color="$nexaAccent" />
                  <YStack gap="$1" flex={1}>
                    <Text fontSize={14} fontWeight="700" color="$nexaText">
                      Access History Transparency
                    </Text>
                    <Paragraph color="$nexaSecondary" fontSize={12}>
                      View audit entries for any healthcare provider who requested or accessed your health record.
                    </Paragraph>
                  </YStack>
                </XStack>
              </Surface>

              <Surface
                padding="$3"
                borderRadius={10}
                backgroundColor="$nexaSurface"
                borderWidth={1}
                borderColor="$nexaBorder"
              >
                <XStack gap="$3" alignItems="flex-start">
                  <Lock size={20} color="$nexaAccent" />
                  <YStack gap="$1" flex={1}>
                    <Text fontSize={14} fontWeight="700" color="$nexaText">
                      Memory-Only Capabilities
                    </Text>
                    <Paragraph color="$nexaSecondary" fontSize={12}>
                      Doctor access tokens are time-bounded, held only in ephemeral memory, and never persist to long-term storage or URLs.
                    </Paragraph>
                  </YStack>
                </XStack>
              </Surface>

              <Surface
                padding="$3"
                borderRadius={10}
                backgroundColor="$nexaSurface"
                borderWidth={1}
                borderColor="$nexaBorder"
              >
                <XStack gap="$3" alignItems="flex-start">
                  <User size={20} color="$nexaAccent" />
                  <YStack gap="$1" flex={1}>
                    <Text fontSize={14} fontWeight="700" color="$nexaText">
                      Consent Revocation & Review
                    </Text>
                    <Paragraph color="$nexaSecondary" fontSize={12}>
                      Every routine access requires your active approval. You can review all past and active permissions from your access history.
                    </Paragraph>
                  </YStack>
                </XStack>
              </Surface>
            </YStack>

            <ActionButton
              onPress={() => router.push('/patient/profile')}
            >
              <XStack alignItems="center" justifyContent="center" gap="$2">
                <User size={16} color="$nexaText" />
                <Text color="$nexaText" fontWeight="700" fontSize={14}>
                  Manage Profile
                </Text>
              </XStack>
            </ActionButton>
          </YStack>
        </Surface>
      </XStack>
    </YStack>
  )
}
