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
  ArrowUpRight,
  ShieldAlert,
  UserCheck,
} from '@tamagui/lucide-icons'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { useProviderAuth } from './ProviderAuthContext'

const actions = [
  {
    title: 'Find a patient',
    description: 'Start with a Nexa patient ID, then request access.',
    label: 'Search Patient',
    icon: Search,
    route: '/doctor/patient-search',
  },
  {
    title: 'Scan an NFC card',
    description: 'Discover a patient before requesting their consent.',
    label: 'Scan NFC Card',
    icon: RadioReceiver,
    route: '/doctor/patient-search?mode=nfc',
  },
  {
    title: 'Upload a document',
    description: 'Request document access, upload, and review extracted information.',
    label: 'Upload & AI Extract',
    icon: FileText,
    route: '/doctor/patient-search?intent=document_upload',
  },
  {
    title: 'Review source evidence',
    description: 'Open the adjudication workspace to review assigned cases.',
    label: 'Source adjudication workspace',
    icon: ClipboardCheck,
    route: '/doctor/pipeline/adjudication',
  },
  {
    title: 'Registration recovery review',
    description: 'Review and adjudicate flagged patient account registration-recovery cases.',
    label: 'Open Review Queue',
    icon: UserCheck,
    route: '/doctor/recovery-review',
  },
]

export function DoctorDashboardScreen() {
  const router = useRouter()
  const { hydrated, isAuthenticated, displayName, hospitalName, role } = useProviderAuth()
  useEffect(() => {
    if (hydrated && !isAuthenticated) router.replace('/doctor/login')
  }, [hydrated, isAuthenticated, router])

  if (!hydrated || !isAuthenticated) return <LoadingState label="Opening your workspace..." />

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="CLINICAL COMMAND CENTER"
        title="Ready for your next patient"
        description="Find a patient, request scoped consent, and review authentic clinical records with source provenance."
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

      {/* Clinical Operations Summary */}
      <XStack
        flexWrap="wrap"
        gap="$3.5"
      >
        <Surface
          flex={1}
          minWidth={220}
          padding="$3.5"
          gap="$1"
          backgroundColor="$nexaSurface"
        >
          <Text
            color="$nexaSecondary"
            fontSize={12}
            fontWeight="700"
            textTransform="uppercase"
            letterSpacing={0.5}
          >
            Consent Security
          </Text>
          <Text
            color="$nexaSuccess"
            fontSize={20}
            fontWeight="800"
          >
            Memory-Only
          </Text>
          <Paragraph
            color="$nexaSecondary"
            fontSize={12}
          >
            Capabilities held in memory only; never stored in URLs or browser storage
          </Paragraph>
        </Surface>

        <Surface
          flex={1}
          minWidth={220}
          padding="$3.5"
          gap="$1"
          backgroundColor="$nexaSurface"
        >
          <Text
            color="$nexaSecondary"
            fontSize={12}
            fontWeight="700"
            textTransform="uppercase"
            letterSpacing={0.5}
          >
            Traceability
          </Text>
          <Text
            color="$nexaAccent"
            fontSize={20}
            fontWeight="800"
          >
            Audited Access
          </Text>
          <Paragraph
            color="$nexaSecondary"
            fontSize={12}
          >
            Clinical and audit records recorded server-side
          </Paragraph>
        </Surface>

        <Surface
          flex={1}
          minWidth={220}
          padding="$3.5"
          gap="$1"
          backgroundColor="$nexaSurface"
        >
          <Text
            color="$nexaSecondary"
            fontSize={12}
            fontWeight="700"
            textTransform="uppercase"
            letterSpacing={0.5}
          >
            AI Document Intake
          </Text>
          <Text
            color="$nexaText"
            fontSize={20}
            fontWeight="800"
          >
            Clinician Adjudication
          </Text>
          <Paragraph
            color="$nexaSecondary"
            fontSize={12}
          >
            Model predictions require clinician approval
          </Paragraph>
        </Surface>
      </XStack>

      {/* Primary Workflows */}
      <YStack gap="$3">
        <SectionHeading>Start a care workflow</SectionHeading>
        <XStack
          flexWrap="wrap"
          gap="$4"
        >
          {actions.map(({ title, description, label, icon: Icon, route }) => (
            <Surface
              key={route}
              flexBasis={400}
              flexGrow={1}
              flexShrink={1}
              minWidth={0}
              padding="$4.5"
              gap="$3"
              hoverStyle={{
                borderColor: '$nexaAccent',
              }}
            >
              <XStack
                alignItems="center"
                justifyContent="space-between"
              >
                <YStack
                  backgroundColor="$nexaAccentSoft"
                  padding="$3"
                  borderRadius={12}
                >
                  <Icon
                    size={24}
                    color="$nexaAccent"
                  />
                </YStack>
                <ArrowUpRight
                  size={20}
                  color="$nexaSecondary"
                />
              </XStack>
              <YStack gap="$1">
                <SectionHeading>{title}</SectionHeading>
                <Paragraph
                  color="$nexaSecondary"
                  fontSize={14}
                  lineHeight={22}
                  flex={1}
                >
                  {description}
                </Paragraph>
              </YStack>
              <ActionButton
                intent="primary"
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

