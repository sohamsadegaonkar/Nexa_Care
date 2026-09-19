'use client'

import { useRouter } from 'next/navigation'
import {
  ActionButton,
  H2,
  InlineNotice,
  Paragraph,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { ArrowLeft, ShieldCheck, Smartphone } from '@tamagui/lucide-icons'

export default function WebTreatmentRequestPage() {
  const router = useRouter()

  return (
    <YStack gap="$5" maxWidth={680} width="100%" marginHorizontal="auto" padding="$4">
      {/* Back Button */}
      <XStack alignItems="center" gap="$2">
        <ActionButton
          chromeless
          paddingHorizontal="$2"
          onPress={() => router.push('/patient/dashboard')}
          accessibilityLabel="Back to Dashboard"
        >
          <XStack alignItems="center" gap="$1.5">
            <ArrowLeft size={16} color="$nexaSecondary" />
            <Text color="$nexaSecondary" fontSize={13} fontWeight="600">
              Dashboard
            </Text>
          </XStack>
        </ActionButton>
      </XStack>

      <Surface padding="$6" borderRadius={16} elevation="$1" gap="$4">
        <XStack gap="$3" alignItems="center">
          <Surface
            padding="$3"
            borderRadius={12}
            backgroundColor="$nexaMuted"
          >
            <Smartphone size={28} color="$nexaAccent" />
          </Surface>
          <YStack gap="$0.5">
            <Text
              fontSize={12}
              fontWeight="700"
              color="$nexaSecondary"
              textTransform="uppercase"
              letterSpacing={1}
            >
              TREATMENT SESSION APPROVAL
            </Text>
            <H2 fontSize={22} fontWeight="900" color="$nexaText">
              Mobile Device Required
            </H2>
          </YStack>
        </XStack>

        <InlineNotice
          tone="neutral"
          title="Hardware-backed security boundary"
          description="Treatment session approvals require your enrolled Nexa mobile device with hardware-backed biometric signing."
        />

        <Paragraph color="$nexaSecondary" fontSize={14} lineHeight={22}>
          For your security, provider treatment requests can only be approved or denied on your enrolled smartphone using your device's biometric authentication (fingerprint, Face ID, or secure lock).
        </Paragraph>

        <Paragraph color="$nexaSecondary" fontSize={14} lineHeight={22}>
          Cryptographic signing keys are non-exportable and safely isolated inside your phone's hardware security module. They cannot be used or exported through a web browser.
        </Paragraph>

        <Surface
          padding="$3.5"
          borderRadius={10}
          backgroundColor="$nexaSurface"
          borderWidth={1}
          borderColor="$nexaBorder"
        >
          <YStack gap="$1.5">
            <Text fontSize={13} fontWeight="700" color="$nexaText">
              How to approve a treatment session:
            </Text>
            <Text fontSize={13} color="$nexaSecondary">
              1. Open the Nexa Care app on your enrolled phone.
            </Text>
            <Text fontSize={13} color="$nexaSecondary">
              2. Tap on "Treatment Approval" on your Health Home.
            </Text>
            <Text fontSize={13} color="$nexaSecondary">
              3. Enter the request ID provided by your clinician and verify with your biometrics.
            </Text>
          </YStack>
        </Surface>

        <XStack gap="$3" flexWrap="wrap" marginTop="$2">
          <ActionButton
            intent="primary"
            minHeight={44}
            onPress={() => router.push('/patient/treatment-access')}
            accessibilityLabel="View Treatment Access"
          >
            <XStack alignItems="center" gap="$2">
              <ShieldCheck size={16} color="white" />
              <Text color="white" fontSize={14} fontWeight="700">
                View Treatment Access
              </Text>
            </XStack>
          </ActionButton>

          <ActionButton
            minHeight={44}
            onPress={() => router.push('/patient/dashboard')}
            accessibilityLabel="Return to Dashboard"
          >
            <Text fontSize={14} fontWeight="600" color="$nexaText">
              Return to Dashboard
            </Text>
          </ActionButton>
        </XStack>
      </Surface>
    </YStack>
  )
}
