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
  FlaskConical,
  Heart,
  Lock,
  Pill,
  QrCode,
  Shield,
  ShieldAlert,
  ShieldCheck,
  User,
  UserCheck,
} from '@tamagui/lucide-icons'
import { getCurrentPatientId, usePatientAuthSession } from 'app/services/patientAuthSession'
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

export default function PatientDashboardPage() {
  const router = useRouter()
  const session = usePatientAuthSession()
  const patientId = getCurrentPatientId()
  const [accessLogs, setAccessLogs] = useState<AccessLogItem[]>([])
  const [loading, setLoading] = useState(false)
  const [timelineCount, setTimelineCount] = useState(0)
  const [publicPatientId, setPublicPatientId] = useState<string | null>(null)
  const [healthSummary, setHealthSummary] = useState<PatientHealthSummaryResponse | null>(null)

  useEffect(() => {
    let mounted = true
    async function loadData() {
      if (session.status !== 'authenticated') return
      setLoading(true)

      // Fetch health summary
      try {
        const summaryData = await NexaApiClient.getMyHealthSummary()
        if (mounted) {
          setHealthSummary(summaryData)
        }
      } catch {
        // fallback gracefully
      }

      // Fetch access history
      try {
        const historyRes = await apiClient.get<any>('/api/v2/patient/me/access-history?limit=3')
        const raw = (historyRes as any)?.data?.access_history || (historyRes as any)?.access_history || []
        if (mounted && Array.isArray(raw)) {
          setAccessLogs(raw.slice(0, 3))
        }
      } catch {
        // graceful fallback
      }

      // Fetch timeline count
      try {
        const timelineRes = await apiClient.get<any>('/api/v2/patient/me/timeline?limit=10')
        const rawEvents = (timelineRes as any)?.data?.events || (timelineRes as any)?.events || []
        if (mounted && Array.isArray(rawEvents)) {
          setTimelineCount(rawEvents.length)
        }
      } catch {
        // graceful fallback
      }

      // Fetch profile
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
      {/* Welcome & Patient Identity Banner */}
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
              My Health Home
            </Text>
            <Paragraph color="$nexaSecondary" fontSize={14}>
              Your longitudinal health record, active prescriptions, clinical observations, and verifiable access logs.
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

      {/* Quick Longitudinal Hub Action Tiles */}
      <XStack flexWrap="wrap" gap="$3">
        <Surface
          flex={1}
          minWidth={180}
          padding="$4"
          borderRadius={14}
          elevation="$1"
          pressStyle={{ opacity: 0.85 }}
          onPress={() => router.push('/patient/timeline')}
        >
          <XStack justifyContent="space-between" alignItems="center">
            <YStack gap="$1">
              <Text fontSize={24}>📅</Text>
              <Text fontSize={16} fontWeight="800" color="$nexaText">
                Timeline
              </Text>
              <Paragraph fontSize={12} color="$nexaSecondary">
                {timelineCount} clinical events
              </Paragraph>
            </YStack>
            <ArrowRight size={18} color="$nexaSecondary" />
          </XStack>
        </Surface>

        <Surface
          flex={1}
          minWidth={180}
          padding="$4"
          borderRadius={14}
          elevation="$1"
          pressStyle={{ opacity: 0.85 }}
          onPress={() => router.push('/patient/records')}
        >
          <XStack justifyContent="space-between" alignItems="center">
            <YStack gap="$1">
              <Text fontSize={24}>📁</Text>
              <Text fontSize={16} fontWeight="800" color="$nexaText">
                Records
              </Text>
              <Paragraph fontSize={12} color="$nexaSecondary">
                Categorized observations
              </Paragraph>
            </YStack>
            <ArrowRight size={18} color="$nexaSecondary" />
          </XStack>
        </Surface>

        <Surface
          flex={1}
          minWidth={180}
          padding="$4"
          borderRadius={14}
          elevation="$1"
          pressStyle={{ opacity: 0.85 }}
          onPress={() => router.push('/patient/prescriptions')}
        >
          <XStack justifyContent="space-between" alignItems="center">
            <YStack gap="$1">
              <Text fontSize={24}>💊</Text>
              <Text fontSize={16} fontWeight="800" color="$nexaText">
                Prescriptions
              </Text>
              <Paragraph fontSize={12} color="$nexaSecondary">
                {healthSummary?.counts?.medications ?? 0} medications on file
              </Paragraph>
            </YStack>
            <ArrowRight size={18} color="$nexaSecondary" />
          </XStack>
        </Surface>

        <Surface
          flex={1}
          minWidth={180}
          padding="$4"
          borderRadius={14}
          elevation="$1"
          pressStyle={{ opacity: 0.85 }}
          onPress={() => router.push('/patient/reports')}
        >
          <XStack justifyContent="space-between" alignItems="center">
            <YStack gap="$1">
              <Text fontSize={24}>📄</Text>
              <Text fontSize={16} fontWeight="800" color="$nexaText">
                Reports
              </Text>
              <Paragraph fontSize={12} color="$nexaSecondary">
                {healthSummary?.counts?.reports ?? 0} diagnostic files
              </Paragraph>
            </YStack>
            <ArrowRight size={18} color="$nexaSecondary" />
          </XStack>
        </Surface>
      </XStack>

      {/* Metrics Row */}
      <XStack flexWrap="wrap" gap="$4">
        <StatCard
          label="Active Medications"
          value={
            healthSummary?.active_medications?.length
              ? `${healthSummary.active_medications.length} Prescribed`
              : '0 Active'
          }
          tone="neutral"
          icon={<Pill size={20} />}
          description="Clinician-directed treatments"
        />
        <StatCard
          label="Allergies on File"
          value={
            healthSummary?.allergy_highlights?.length
              ? `${healthSummary.allergy_highlights.length} Recorded`
              : '0 Recorded'
          }
          tone={healthSummary?.allergy_highlights?.length ? 'warning' : 'success'}
          icon={<AlertTriangle size={20} />}
          description="Clinical allergy alerts"
        />
        <StatCard
          label="Recent Labs"
          value={
            healthSummary?.recent_labs?.length
              ? `${healthSummary.recent_labs.length} Evaluated`
              : '0 Evaluated'
          }
          tone="accent"
          icon={<FlaskConical size={20} />}
          description="Blood, pathology, and diagnostic tests"
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
                  Active medications, recorded allergies, and recent vital readings
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
                💊 Active Medications
              </Text>
              {healthSummary?.active_medications && healthSummary.active_medications.length > 0 ? (
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
                  No active medications recorded on file.
                </Paragraph>
              )}
            </YStack>

            {/* Allergies Highlight */}
            <YStack gap="$2.5">
              <Text fontSize={14} fontWeight="700" color="$nexaText">
                ⚠️ Allergies & Sensitivities
              </Text>
              {healthSummary?.allergy_highlights && healthSummary.allergy_highlights.length > 0 ? (
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
                  No recorded allergies on file.
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
                    Zero-Trust Access Model
                  </Text>
                  <Paragraph color="$nexaSecondary" fontSize={12}>
                    Healthcare providers cannot browse your records without active consent tokens or verified break-glass justification.
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
