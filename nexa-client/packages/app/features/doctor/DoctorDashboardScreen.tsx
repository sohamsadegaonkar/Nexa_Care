'use client'

import {
  ActionButton,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenContainer,
  ScreenHeader,
  SectionHeading,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import {
  FileText,
  Search,
  RadioReceiver,
  ClipboardCheck,
  ShieldAlert,
  UserCheck,
  HeartPulse,
  Clock,
  Activity,
  ArrowUpRight,
  ShieldCheck,
  RotateCw,
} from '@tamagui/lucide-icons'
import { useRouter } from 'next/navigation'
import { useEffect, useState, useCallback } from 'react'
import { useProviderAuth } from './ProviderAuthContext'
import {
  NexaApiClient,
  type ProviderWorkspaceResponse,
  type ActiveTreatmentSessionItem,
  type RecentEncounterItem,
} from '../../utils/apiClient'

const secondaryTools = [
  {
    title: 'Upload external document',
    description: 'Import and review previous prescriptions, labs, or hospital records.',
    label: 'Upload & Extract',
    icon: FileText,
    route: '/doctor/patient-search?intent=document_upload',
  },
  {
    title: 'Needs clinical verification',
    description: 'Review and verify extracted document evidence against authentic source pages.',
    label: 'Verify Records',
    icon: ClipboardCheck,
    route: '/doctor/pipeline/adjudication',
  },
  {
    title: 'Registration recovery review',
    description: 'Review and adjudicate flagged patient account registration-recovery cases.',
    label: 'Recovery Queue',
    icon: UserCheck,
    route: '/doctor/recovery-review',
  },
]

export function DoctorDashboardScreen() {
  const router = useRouter()
  const { hydrated, isAuthenticated, displayName, hospitalName, role, treatmentSession } =
    useProviderAuth()

  const [workspace, setWorkspace] = useState<ProviderWorkspaceResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    if (hydrated && !isAuthenticated) router.replace('/doctor/login')
  }, [hydrated, isAuthenticated, router])

  const fetchWorkspace = useCallback(async () => {
    if (!isAuthenticated) return
    setWorkspaceLoading(true)
    setWorkspaceError(null)
    try {
      const data = await NexaApiClient.getProviderWorkspace()
      setWorkspace(data)
    } catch {
      // Safe generic message — never leak internals or tokens
      setWorkspaceError('Clinical workspace data temporarily unavailable.')
    } finally {
      setWorkspaceLoading(false)
    }
  }, [isAuthenticated])

  useEffect(() => {
    fetchWorkspace()
  }, [fetchWorkspace, refreshKey])

  if (!hydrated || !isAuthenticated) return <LoadingState label="Opening your workspace..." />

  const activeSessions: ActiveTreatmentSessionItem[] = workspace?.active_sessions || []
  const recentEncounters: RecentEncounterItem[] = workspace?.recent_encounters || []

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="CLINICAL WORKSPACE"
        title="Ready for your next patient"
        description="Active treatment sessions, patient discovery, and authorized clinical care."
      />

      {/* Provider Status & Trust Banner */}
      <Surface
        backgroundColor="$nexaSurface"
        borderColor="$nexaBorder"
        padding="$4"
      >
        <XStack
          flexWrap="wrap"
          alignItems="center"
          justifyContent="space-between"
          gap="$4"
        >
          <XStack
            alignItems="center"
            gap="$3.5"
            flexWrap="wrap"
          >
            <YStack
              width={48}
              height={48}
              borderRadius={24}
              backgroundColor="$nexaAccentSoft"
              alignItems="center"
              justifyContent="center"
            >
              <Text
                color="$nexaAccent"
                fontSize={20}
                fontWeight="800"
              >
                {(displayName || 'Dr')[0]?.toUpperCase()}
              </Text>
            </YStack>
            <YStack gap="$0.5">
              <Text
                color="$nexaText"
                fontWeight="800"
                fontSize={19}
              >
                {displayName || 'Provider name unavailable'}
              </Text>
              <Paragraph
                color="$nexaSecondary"
                fontSize={14}
              >
                {hospitalName || 'Facility name unavailable'}
              </Paragraph>
            </YStack>
          </XStack>
          <XStack
            gap="$2"
            alignItems="center"
            flexWrap="wrap"
          >
            <StatusBadge tone="success">
              Provider session active
            </StatusBadge>
            <StatusBadge tone="info">
              {role ? `Role: ${role}` : 'Role: Clinician'}
            </StatusBadge>
          </XStack>
        </XStack>
      </Surface>

      {/* Primary Clinical Actions */}
      <YStack gap="$3">
        <SectionHeading>Patient Discovery & Care Initiation</SectionHeading>
        <XStack
          flexWrap="wrap"
          gap="$3.5"
        >
          <Surface
            flex={1}
            minWidth={220}
            padding="$4"
            gap="$2"
            backgroundColor="$nexaSurface"
            hoverStyle={{ borderColor: '$nexaAccent' }}
          >
            <XStack
              alignItems="center"
              justifyContent="space-between"
            >
              <YStack
                backgroundColor="$nexaAccentSoft"
                padding="$2.5"
                borderRadius={10}
              >
                <Search
                  size={22}
                  color="$nexaAccent"
                />
              </YStack>
              <ArrowUpRight
                size={18}
                color="$nexaSecondary"
              />
            </XStack>
            <Text
              color="$nexaText"
              fontSize={16}
              fontWeight="800"
            >
              Find Patient
            </Text>
            <Paragraph
              color="$nexaSecondary"
              fontSize={13}
            >
              Search by Nexa patient identifier to request clinical access.
            </Paragraph>
            <ActionButton
              intent="primary"
              onPress={() => router.push('/doctor/patient-search')}
            >
              Search Patient
            </ActionButton>
          </Surface>

          <Surface
            flex={1}
            minWidth={220}
            padding="$4"
            gap="$2"
            backgroundColor="$nexaSurface"
            hoverStyle={{ borderColor: '$nexaAccent' }}
          >
            <XStack
              alignItems="center"
              justifyContent="space-between"
            >
              <YStack
                backgroundColor="$nexaAccentSoft"
                padding="$2.5"
                borderRadius={10}
              >
                <RadioReceiver
                  size={22}
                  color="$nexaAccent"
                />
              </YStack>
              <ArrowUpRight
                size={18}
                color="$nexaSecondary"
              />
            </XStack>
            <Text
              color="$nexaText"
              fontSize={16}
              fontWeight="800"
            >
              Scan NFC Card
            </Text>
            <Paragraph
              color="$nexaSecondary"
              fontSize={13}
            >
              Tap patient health card for contactless identity discovery.
            </Paragraph>
            <ActionButton
              intent="primary"
              onPress={() => router.push('/doctor/patient-search?mode=nfc')}
            >
              Scan NFC Card
            </ActionButton>
          </Surface>

          <Surface
            flex={1}
            minWidth={220}
            padding="$4"
            gap="$2"
            backgroundColor="$nexaSurface"
            hoverStyle={{ borderColor: '$nexaAccent' }}
          >
            <XStack
              alignItems="center"
              justifyContent="space-between"
            >
              <YStack
                backgroundColor="$nexaAccentSoft"
                padding="$2.5"
                borderRadius={10}
              >
                <HeartPulse
                  size={22}
                  color="$nexaAccent"
                />
              </YStack>
              <ArrowUpRight
                size={18}
                color="$nexaSecondary"
              />
            </XStack>
            <Text
              color="$nexaText"
              fontSize={16}
              fontWeight="800"
            >
              Record Treatment Vitals
            </Text>
            <Paragraph
              color="$nexaSecondary"
              fontSize={13}
            >
              Request a patient-signed session to record clinical observations.
            </Paragraph>
            <ActionButton
              intent="primary"
              onPress={() => router.push('/doctor/patient-search?intent=treatment_vitals')}
            >
              Start Vitals Session
            </ActionButton>
          </Surface>
        </XStack>
      </YStack>

      {/* TODAY / ACTIVE CARE SECTION */}
      <YStack gap="$4">
        <XStack
          alignItems="center"
          justifyContent="space-between"
        >
          <SectionHeading>Active Treatment Sessions</SectionHeading>
          <ActionButton
            onPress={() => setRefreshKey((k) => k + 1)}
          >
            Refresh
          </ActionButton>
        </XStack>

        {workspaceLoading ? (
          <Surface
            padding="$4"
            alignItems="center"
            justifyContent="center"
            backgroundColor="$nexaSurface"
          >
            <Paragraph color="$nexaSecondary">Checking active sessions...</Paragraph>
          </Surface>
        ) : workspaceError ? (
          <Surface
            padding="$4"
            backgroundColor="$nexaSurface"
            borderColor="$nexaBorder"
          >
            <Paragraph color="$nexaSecondary">{workspaceError}</Paragraph>
          </Surface>
        ) : activeSessions.length === 0 ? (
          <Surface
            padding="$4.5"
            backgroundColor="$nexaSurface"
            borderColor="$nexaBorder"
            gap="$2"
          >
            <XStack
              alignItems="center"
              gap="$2.5"
            >
              <Activity
                size={20}
                color="$nexaSecondary"
              />
              <Text
                color="$nexaText"
                fontWeight="700"
                fontSize={15}
              >
                No active treatment sessions right now
              </Text>
            </XStack>
            <Paragraph
              color="$nexaSecondary"
              fontSize={13}
            >
              When a patient approves a treatment session or consultation on their device, it will appear here with active clinical capability. Use Find Patient or Scan NFC Card above to start.
            </Paragraph>
          </Surface>
        ) : (
          <YStack gap="$3">
            {activeSessions.map((session) => {
              const hasLocalCapability =
                treatmentSession?.treatmentToken &&
                (treatmentSession.patientDisplayIdentifier === session.patient_display_identifier ||
                  treatmentSession.requestId === session.session_id)

              return (
                <Surface
                  key={session.session_id}
                  padding="$4"
                  backgroundColor="$nexaSurface"
                  borderColor="$nexaBorder"
                  gap="$3"
                >
                  <XStack
                    flexWrap="wrap"
                    alignItems="center"
                    justifyContent="space-between"
                    gap="$3"
                  >
                    <YStack gap="$1">
                      <XStack
                        alignItems="center"
                        gap="$2.5"
                        flexWrap="wrap"
                      >
                        <Text
                          color="$nexaText"
                          fontSize={16}
                          fontWeight="800"
                        >
                          {session.patient_name || 'Patient'}
                        </Text>
                        <StatusBadge tone="info">
                          {session.patient_display_identifier}
                        </StatusBadge>
                        <StatusBadge tone="success">
                          Active Treatment Session
                        </StatusBadge>
                      </XStack>
                      <XStack
                        gap="$3"
                        alignItems="center"
                        flexWrap="wrap"
                      >
                        <Text
                          color="$nexaSecondary"
                          fontSize={12}
                        >
                          Purpose: {session.purpose}
                        </Text>
                        <Text
                          color="$nexaSecondary"
                          fontSize={12}
                        >
                          Expires: {new Date(session.expires_at).toLocaleTimeString()}
                        </Text>
                        {session.allowed_operations && session.allowed_operations.length > 0 && (
                          <Text
                            color="$nexaSecondary"
                            fontSize={12}
                          >
                            Operations: {session.allowed_operations.join(', ')}
                          </Text>
                        )}
                      </XStack>
                    </YStack>

                    <XStack
                      gap="$2"
                      alignItems="center"
                    >
                      {hasLocalCapability ? (
                        <ActionButton
                          intent="primary"
                          onPress={() =>
                            router.push('/doctor/treatment-vitals')
                          }
                        >
                          Continue Consultation
                        </ActionButton>
                      ) : (
                        <ActionButton
                          onPress={() =>
                            router.push('/doctor/patient-search')
                          }
                        >
                          Request New Access
                        </ActionButton>
                      )}
                    </XStack>
                  </XStack>
                </Surface>
              )
            })}
          </YStack>
        )}
      </YStack>

      {/* RECENT CLINICAL ENCOUNTERS */}
      {recentEncounters.length > 0 && (
        <YStack gap="$3">
          <SectionHeading>Recent Clinical Encounters</SectionHeading>
          <YStack gap="$2.5">
            {recentEncounters.map((enc) => (
              <Surface
                key={enc.encounter_id}
                padding="$3.5"
                backgroundColor="$nexaSurface"
                borderColor="$nexaBorder"
              >
                <XStack
                  alignItems="center"
                  justifyContent="space-between"
                  flexWrap="wrap"
                  gap="$3"
                >
                  <YStack gap="$0.5">
                    <XStack
                      alignItems="center"
                      gap="$2"
                    >
                      <Text
                        color="$nexaText"
                        fontWeight="700"
                        fontSize={14}
                      >
                        {enc.patient_name || 'Patient'}
                      </Text>
                      <StatusBadge tone="neutral">
                        {enc.patient_display_identifier}
                      </StatusBadge>
                    </XStack>
                    <Text
                      color="$nexaSecondary"
                      fontSize={12}
                    >
                      Encounter recorded: {new Date(enc.created_at).toLocaleString()}
                    </Text>
                  </YStack>
                  <ActionButton
                    onPress={() =>
                      router.push('/doctor/patient-search')
                    }
                  >
                    Open Patient Workspace
                  </ActionButton>
                </XStack>
              </Surface>
            ))}
          </YStack>
        </YStack>
      )}

      {/* Pending Access Notice */}
      <Surface
        backgroundColor="$nexaSurface"
        borderColor="$nexaBorder"
        padding="$4"
        gap="$2"
      >
        <XStack
          alignItems="center"
          gap="$2.5"
        >
          <Clock
            size={18}
            color="$nexaAccent"
          />
          <Text
            color="$nexaText"
            fontWeight="700"
            fontSize={14}
          >
            Pending Patient Access
          </Text>
        </XStack>
        <Paragraph
          color="$nexaSecondary"
          fontSize={13}
        >
          Nexa Care operates under a zero-trust, consent-first boundary. Access requests are evaluated in real time on the patient's device and expire automatically if unapproved.
        </Paragraph>
      </Surface>

      {/* Secondary Tools */}
      <YStack gap="$3">
        <SectionHeading>Secondary Clinical Tools</SectionHeading>
        <XStack
          flexWrap="wrap"
          gap="$4"
        >
          {secondaryTools.map(({ title, description, label, icon: Icon, route }) => (
            <Surface
              key={route}
              flexBasis={300}
              flexGrow={1}
              flexShrink={1}
              minWidth={0}
              padding="$4"
              gap="$2.5"
              backgroundColor="$nexaSurface"
              hoverStyle={{ borderColor: '$nexaAccent' }}
            >
              <XStack
                alignItems="center"
                justifyContent="space-between"
              >
                <YStack
                  backgroundColor="$nexaAccentSoft"
                  padding="$2.5"
                  borderRadius={10}
                >
                  <Icon
                    size={22}
                    color="$nexaAccent"
                  />
                </YStack>
                <ArrowUpRight
                  size={18}
                  color="$nexaSecondary"
                />
              </XStack>
              <YStack gap="$1">
                <Text
                  color="$nexaText"
                  fontWeight="700"
                  fontSize={15}
                >
                  {title}
                </Text>
                <Paragraph
                  color="$nexaSecondary"
                  fontSize={13}
                  lineHeight={20}
                >
                  {description}
                </Paragraph>
              </YStack>
              <ActionButton
                onPress={() => router.push(route)}
              >
                {label}
              </ActionButton>
            </Surface>
          ))}
        </XStack>
      </YStack>

      {/* Emergency Break-Glass Panel */}
      <Surface
        backgroundColor="$nexaDangerSoft"
        borderColor="$nexaDanger"
        padding="$4.5"
        gap="$3"
      >
        <XStack
          gap="$3"
          alignItems="center"
        >
          <YStack
            padding="$2.5"
            borderRadius={10}
            backgroundColor="rgba(165,44,57,0.15)"
          >
            <ShieldAlert
              size={26}
              color="$nexaDanger"
            />
          </YStack>
          <YStack gap="$0.5">
            <SectionHeading>Emergency break-glass access</SectionHeading>
            <Text
              color="$nexaDanger"
              fontSize={13}
              fontWeight="700"
            >
              For life-threatening situations where routine patient consent is impossible
            </Text>
          </YStack>
        </XStack>
        <Paragraph
          color="$nexaDanger"
          fontSize={14}
          lineHeight={22}
        >
          Requires an approved emergency reason code and clinical justification. Access is limited to minimum-necessary emergency categories, strictly time-bounded, and immediately recorded in the patient's permanent access history.
        </Paragraph>
        <ActionButton
          intent="danger"
          alignSelf="flex-start"
          onPress={() => router.push('/doctor/emergency-access')}
        >
          Review emergency access
        </ActionButton>
      </Surface>

      <InlineNotice title="Patient permission comes first">
        Routine record access and document workflows require the patient's explicit approval. Your current
        permissions are verified server-side for each protected clinical action.
      </InlineNotice>
    </ScreenContainer>
  )
}
