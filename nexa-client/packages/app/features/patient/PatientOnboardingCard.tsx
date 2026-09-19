import React from 'react'
import { useRouter } from 'solito/navigation'
import {
  Button,
  H3,
  Paragraph,
  Text,
  XStack,
  YStack,
} from 'tamagui'

export interface PatientOnboardingCardProps {
  /** Optional callback invoked when the patient chooses to skip adding records. */
  onSkip?: () => void
  /** Optional callback invoked when the patient chooses to add a record. */
  onAddRecord?: () => void
  /** Controls whether the card is embedded inside an onboarding screen or standalone. */
  variant?: 'card' | 'screen-step'
}

/**
 * PatientOnboardingCard
 *
 * An optional, non-coercive onboarding card inviting new patients to import
 * previous medical records (prescriptions, diagnostic reports).
 *
 * Invariants:
 * - Strictly optional: clearly communicates that records can be added later.
 * - No dark patterns: "Skip for now" is equally accessible and visible.
 * - Navigates to `/patient/records/import?returnTo=onboarding` using the closed returnTo contract.
 * - Accessible touch targets >= 44px, screen-reader role="region".
 */
export default function PatientOnboardingCard({
  onSkip,
  onAddRecord,
  variant = 'card',
}: PatientOnboardingCardProps) {
  const router = useRouter()

  const handleAddPress = () => {
    if (onAddRecord) {
      onAddRecord()
    } else {
      router.push('/patient/records/import?returnTo=onboarding')
    }
  }

  const handleSkipPress = () => {
    if (onSkip) {
      onSkip()
    } else {
      router.push('/patient/dashboard')
    }
  }

  return (
    <YStack
      role="region"
      aria-label="Optional medical records onboarding step"
      backgroundColor="$backgroundHover"
      borderWidth={1}
      borderColor="$borderColor"
      borderRadius="$5"
      padding="$5"
      gap="$4"
      maxWidth={variant === 'screen-step' ? 540 : '100%'}
      width="100%"
    >
      {/* Header with Optional Badge */}
      <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
        <XStack alignItems="center" gap="$2.5">
          <Text fontSize={28} accessibilityLabel="Medical documents icon">
            📁
          </Text>
          <YStack>
            <H3 color="$color" fontSize="$5" fontWeight="800">
              Do you have previous medical records?
            </H3>
          </YStack>
        </XStack>
        <YStack
          backgroundColor="$blue4"
          paddingHorizontal="$2.5"
          paddingVertical="$1"
          borderRadius="$2"
          borderWidth={1}
          borderColor="$blue8"
        >
          <Text color="$blue11" fontSize="$1" fontWeight="800">
            OPTIONAL
          </Text>
        </YStack>
      </XStack>

      {/* Explanatory Body */}
      <Paragraph color="$color11" size="$3" lineHeight={22}>
        You can upload previous prescriptions, diagnostic reports, or medical
        documents now to build your longitudinal health timeline. This step is
        entirely optional — you can always add records later from the Medical
        Records tab.
      </Paragraph>

      {/* Actions */}
      <XStack
        gap="$3"
        alignItems="center"
        justifyContent="flex-end"
        flexWrap="wrap"
        paddingTop="$2"
      >
        <Button
          size="$3.5"
          chromeless
          onPress={handleSkipPress}
          accessibilityRole="button"
          accessibilityLabel="Skip adding records for now"
          aria-label="Skip adding records for now"
          minHeight={44}
          paddingHorizontal="$4"
        >
          <Text color="$color10" fontWeight="600" fontSize="$3">
            Skip for now
          </Text>
        </Button>

        <Button
          size="$3.5"
          theme="blue"
          onPress={handleAddPress}
          accessibilityRole="button"
          accessibilityLabel="Add Medical Record"
          aria-label="Add Medical Record"
          minHeight={44}
          paddingHorizontal="$4"
        >
          <Text color="white" fontWeight="800" fontSize="$3">
            Add Medical Record →
          </Text>
        </Button>
      </XStack>
    </YStack>
  )
}
