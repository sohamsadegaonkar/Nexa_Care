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
        eyebrow="PROVIDER WORKSPACE"
        title="Ready for your next patient"
        description="Find a patient, request access, and bring the right information into focus."
      />
      <Surface>
        <XStack
          flexWrap="wrap"
          alignItems="center"
          justifyContent="space-between"
          gap="$3"
        >
          <YStack gap="$1">
            <Text
              color="$nexaText"
              fontWeight="700"
              fontSize={18}
            >
              {displayName || 'Provider name unavailable'}
            </Text>
            <Paragraph color="$nexaSecondary">
              {hospitalName || 'Facility name unavailable'}
            </Paragraph>
          </YStack>
          <StatusBadge>{role ? `Session role: ${role}` : 'Session role unavailable'}</StatusBadge>
        </XStack>
      </Surface>
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
              <SectionHeading>{title}</SectionHeading>
              <Paragraph
                color="$nexaSecondary"
                fontSize={15}
                lineHeight={24}
                flex={1}
              >
                {description}
              </Paragraph>
              <ActionButton onPress={() => router.push(route)}>{label}</ActionButton>
            </Surface>
          ))}
        </XStack>
      </YStack>
      <Surface
        backgroundColor="$nexaDangerSoft"
        borderColor="$nexaDanger"
      >
        <XStack
          gap="$3"
          alignItems="center"
        >
          <ShieldAlert
            size={24}
            color="$nexaDanger"
          />
          <SectionHeading>Emergency access</SectionHeading>
        </XStack>
        <Paragraph
          color="$nexaDanger"
          fontSize={15}
          lineHeight={24}
        >
          For urgent care when routine consent cannot be obtained. Access is limited, time-bound,
          and recorded in the patient's access history.
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
        Routine record access and document workflows require the patient's approval. Your current
        permissions are checked for each protected action.
      </InlineNotice>
    </ScreenContainer>
  )
}
