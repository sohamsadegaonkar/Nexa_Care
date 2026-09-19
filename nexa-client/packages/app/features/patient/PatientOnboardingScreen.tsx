import React from 'react'
import { useRouter } from 'solito/navigation'
import { ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import {
  Button,
  H2,
  Paragraph,
  Separator,
  Text,
  XStack,
  YStack,
} from 'tamagui'
import PatientOnboardingCard from './PatientOnboardingCard'

/**
 * PatientOnboardingScreen
 *
 * Cross-platform onboarding screen for new patients, providing a clean
 * introduction to Nexa Care and offering the optional previous medical records
 * import step.
 */
export default function PatientOnboardingScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()

  return (
    <YStack flex={1} backgroundColor="$background">
      {/* Top Header */}
      <YStack
        paddingHorizontal="$4"
        paddingTop={insets.top + 16}
        paddingBottom="$3"
        borderBottomWidth={1}
        borderBottomColor="$borderColor"
        backgroundColor="$background"
        gap="$1"
      >
        <XStack justifyContent="space-between" alignItems="center">
          <Text color="$color10" fontSize="$2" fontWeight="700">
            PATIENT ONBOARDING
          </Text>
          <Button
            size="$2.5"
            chromeless
            onPress={() => router.push('/patient/dashboard')}
            accessibilityRole="button"
            accessibilityLabel="Skip to Health Dashboard"
          >
            Skip to Dashboard →
          </Button>
        </XStack>
        <H2 color="$color" fontSize="$6" fontWeight="800">
          Welcome to Nexa Care
        </H2>
        <Paragraph color="$color10" size="$3">
          Your account is active and your identity is securely established.
        </Paragraph>
      </YStack>

      <ScrollView
        contentContainerStyle={{
          paddingHorizontal: 20,
          paddingTop: 24,
          paddingBottom: insets.bottom + 48,
          alignItems: 'center',
          gap: 24,
        }}
      >
        {/* Onboarding Welcome Overview */}
        <YStack
          width="100%"
          maxWidth={540}
          backgroundColor="$backgroundHover"
          borderWidth={1}
          borderColor="$borderColor"
          borderRadius="$4"
          padding="$4"
          gap="$2.5"
        >
          <XStack alignItems="center" gap="$2.5">
            <Text fontSize={22}>🛡️</Text>
            <Text color="$color" fontSize="$4" fontWeight="700">
              Account & Privacy Ready
            </Text>
          </XStack>
          <Paragraph color="$color10" size="$2" lineHeight={20}>
            Your health records in Nexa Care are encrypted and protected by
            strict consent controls. You decide which healthcare providers can
            access your medical history.
          </Paragraph>
        </YStack>

        {/* Step: Optional Medical Records Import */}
        <PatientOnboardingCard
          variant="screen-step"
          onSkip={() => router.push('/patient/dashboard')}
          onAddRecord={() => router.push('/patient/records/import?returnTo=onboarding')}
        />

        <Separator width="100%" maxWidth={540} />

        {/* Continue to Dashboard CTA */}
        <YStack width="100%" maxWidth={540} alignItems="center" gap="$2">
          <Button
            size="$4"
            theme="blue"
            width="100%"
            minHeight={48}
            onPress={() => router.push('/patient/dashboard')}
            accessibilityRole="button"
            accessibilityLabel="Continue to Health Dashboard"
          >
            <Text color="white" fontWeight="800" fontSize="$3">
              Go to Health Dashboard →
            </Text>
          </Button>
          <Paragraph color="$color10" size="$2" textAlign="center">
            You can return and update your profile or import records at any time.
          </Paragraph>
        </YStack>
      </ScrollView>
    </YStack>
  )
}
