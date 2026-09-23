'use client'

import {
  ActionButton,
  AuthFrame,
  Paragraph,
  ScreenHeader,
  Separator,
  YStack,
} from '@my/ui'
import { useRouter } from 'solito/navigation'
import { ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

export interface HomeScreenProps {
  onNavigate?: (screen: string) => void
}

export function HomeScreen({ onNavigate }: HomeScreenProps = {}) {
  const router = useRouter()
  const insets = useSafeAreaInsets()

  const navigate = (screen: string) => {
    if (onNavigate) {
      onNavigate(screen)
    } else {
      router.push(`/${screen.replace(/^\//, '')}`)
    }
  }

  return (
    <ScrollView
      style={{ flex: 1 }}
      contentContainerStyle={{
        flexGrow: 1,
        paddingTop: insets.top,
        paddingBottom: insets.bottom + 24,
      }}
      keyboardShouldPersistTaps="handled"
    >
      <AuthFrame>
        <ScreenHeader
          title="Nexa Care"
          description="Secure access to your care and clinical workflows."
        />

        <YStack
          gap="$4"
          width="100%"
        >
          <ActionButton
            intent="primary"
            onPress={() => navigate('patient/login')}
          >
            Continue as Patient
          </ActionButton>

          <ActionButton
            onPress={() => navigate('provider/login')}
          >
            Continue as Healthcare Provider
          </ActionButton>
        </YStack>

        <Separator borderColor="$nexaBorder" />

        <Paragraph
          color="$nexaSecondary"
          fontSize={13}
          lineHeight={20}
          textAlign="center"
        >
          Nexa Care safeguards health information with cryptographic consent, patient-controlled access, and immutable audit integrity.
        </Paragraph>
      </AuthFrame>
    </ScrollView>
  )
}
