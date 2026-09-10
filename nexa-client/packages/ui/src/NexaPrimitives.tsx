import type { ReactNode } from 'react'
import {
  Button,
  H1,
  H2,
  Input,
  Label,
  Paragraph,
  Spinner,
  Text,
  XStack,
  YStack,
  styled,
  type GetProps,
  type InputProps,
  type YStackProps,
} from 'tamagui'

export const ScreenContainer = styled(YStack, {
  name: 'NexaScreen',
  width: '100%',
  maxWidth: 1160,
  alignSelf: 'center',
  padding: '$4',
  gap: '$5',
  $md: { padding: '$6' },
})

export const Surface = styled(YStack, {
  name: 'NexaSurface',
  backgroundColor: '$nexaSurface',
  borderColor: '$nexaBorder',
  borderWidth: 1,
  borderRadius: 16,
  padding: '$4',
  gap: '$3',
})

const ActionFrame = styled(Button, {
  name: 'NexaAction',
  minHeight: 48,
  height: 'auto',
  paddingVertical: '$3',
  borderRadius: 10,
  borderWidth: 1,
  backgroundColor: '$nexaSurface',
  borderColor: '$nexaBorder',
  hoverStyle: { backgroundColor: '$nexaMuted', borderColor: '$nexaAccent' },
  focusVisibleStyle: {
    outlineColor: '$nexaAccent',
    outlineWidth: 3,
    outlineStyle: 'solid',
    outlineOffset: 3,
  },
  disabledStyle: { opacity: 0.55 },
  variants: {
    intent: {
      primary: {
        backgroundColor: '$nexaAccent',
        borderColor: '$nexaAccent',
        hoverStyle: { backgroundColor: '$nexaAccentHover', borderColor: '$nexaAccentHover' },
        pressStyle: { backgroundColor: '$nexaAccentHover' },
      },
      danger: {
        backgroundColor: '$nexaDangerSoft',
        borderColor: '$nexaDanger',
        hoverStyle: { backgroundColor: '$nexaDangerSoft', borderColor: '$nexaDanger' },
      },
    },
  } as const,
})

export function ActionButton({ children, intent, ...props }: GetProps<typeof ActionFrame>) {
  return (
    <ActionFrame
      intent={intent}
      {...props}
    >
      <Button.Text
        color={
          intent === 'primary' ? '$nexaOnAccent' : intent === 'danger' ? '$nexaDanger' : '$nexaText'
        }
        fontWeight="700"
        fontSize={15}
        lineHeight={22}
        whiteSpace="normal"
        textAlign="center"
      >
        {children}
      </Button.Text>
    </ActionFrame>
  )
}

const tones = {
  info: { color: '$nexaAccent', backgroundColor: '$nexaAccentSoft' },
  accent: { color: '$nexaAccent', backgroundColor: '$nexaAccentSoft' },
  neutral: { color: '$nexaSecondary', backgroundColor: '$nexaMuted' },
  success: { color: '$nexaSuccess', backgroundColor: '$nexaSuccessSoft' },
  warning: { color: '$nexaWarning', backgroundColor: '$nexaWarningSoft' },
  danger: { color: '$nexaDanger', backgroundColor: '$nexaDangerSoft' },
} as const
type Tone = keyof typeof tones

export function StatusBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  const selectedTone = tones[tone] ?? tones.neutral
  return (
    <XStack
      alignSelf="flex-start"
      maxWidth="100%"
      borderRadius={6}
      paddingHorizontal="$2"
      paddingVertical="$1"
      backgroundColor={selectedTone.backgroundColor}
    >
      <Text
        color={selectedTone.color}
        fontSize={13}
        lineHeight={20}
        fontWeight="700"
        flexShrink={1}
      >
        {children}
      </Text>
    </XStack>
  )
}

export function InlineNotice({
  title,
  children,
  description,
  tone = 'info',
}: { title: string; children?: ReactNode; description?: ReactNode; tone?: Tone }) {
  const content = children ?? description
  return (
    <YStack
      role={tone === 'danger' ? 'alert' : 'status'}
      backgroundColor={tones[tone].backgroundColor}
      borderLeftWidth={3}
      borderLeftColor={tones[tone].color}
      borderRadius={8}
      padding="$3"
      gap="$1"
    >
      <Text
        color={tones[tone].color}
        fontWeight="700"
        fontSize={15}
      >
        {title}
      </Text>
      {content ? (
        <Paragraph
          color={tones[tone].color}
          fontSize={15}
          lineHeight={23}
        >
          {content}
        </Paragraph>
      ) : null}
    </YStack>
  )
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <XStack
      gap="$3"
      alignItems="center"
    >
      <YStack
        width={40}
        height={40}
        backgroundColor="$nexaAccent"
        borderRadius={12}
        alignItems="center"
        justifyContent="center"
        aria-hidden
      >
        <YStack
          position="absolute"
          width={20}
          height={6}
          borderRadius={2}
          backgroundColor="$nexaOnAccent"
        />
        <YStack
          position="absolute"
          width={6}
          height={20}
          borderRadius={2}
          backgroundColor="$nexaOnAccent"
        />
      </YStack>
      <YStack>
        <Text
          fontSize={22}
          lineHeight={28}
          fontWeight="700"
          letterSpacing={-0.6}
          color="$nexaText"
        >
          Nexa Care
        </Text>
        {!compact && (
          <Text
            fontSize={13}
            color="$nexaSecondary"
          >
            Care connected. Access controlled.
          </Text>
        )}
      </YStack>
    </XStack>
  )
}

export function ScreenHeader({
  eyebrow,
  title,
  description,
  action,
}: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return (
    <XStack
      justifyContent="space-between"
      alignItems="flex-start"
      flexWrap="wrap"
      gap="$3"
    >
      <YStack
        gap="$2"
        flex={1}
        minWidth={200}
      >
        {eyebrow && (
          <Text
            color="$nexaAccent"
            fontSize={13}
            fontWeight="700"
            letterSpacing={1}
          >
            {eyebrow}
          </Text>
        )}
        <H1
          color="$nexaText"
          fontSize={30}
          lineHeight={38}
          letterSpacing={-0.8}
          fontWeight="700"
        >
          {title}
        </H1>
        {description && (
          <Paragraph
            color="$nexaSecondary"
            fontSize={16}
            lineHeight={25}
            maxWidth={660}
          >
            {description}
          </Paragraph>
        )}
      </YStack>
      {action}
    </XStack>
  )
}

export function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <H2
      color="$nexaText"
      fontSize={20}
      lineHeight={28}
      fontWeight="700"
      letterSpacing={-0.3}
    >
      {children}
    </H2>
  )
}

export function FormField({
  id,
  label,
  hint,
  ...props
}: InputProps & { id: string; label: string; hint?: string }) {
  return (
    <YStack gap="$2">
      <Label
        htmlFor={id}
        color="$nexaText"
        fontSize={15}
        lineHeight={22}
        fontWeight="700"
      >
        {label}
      </Label>
      <Input
        id={id}
        aria-label={label}
        aria-describedby={hint ? `${id}-hint` : undefined}
        minHeight={52}
        fontSize={16}
        borderRadius={10}
        backgroundColor="$nexaSurface"
        color="$nexaText"
        borderColor="$nexaBorder"
        placeholderTextColor="$nexaSecondary"
        focusStyle={{
          borderColor: '$nexaAccent',
          outlineColor: '$nexaAccent',
          outlineWidth: 2,
          outlineStyle: 'solid',
          outlineOffset: 2,
        }}
        {...props}
      />
      {hint && (
        <Paragraph
          id={`${id}-hint`}
          color="$nexaSecondary"
          fontSize={13}
          lineHeight={20}
        >
          {hint}
        </Paragraph>
      )}
    </YStack>
  )
}

export function LoadingState({ label }: { label: string }) {
  return (
    <YStack
      aria-live="polite"
      aria-atomic
      aria-label={label}
      alignItems="center"
      justifyContent="center"
      padding="$6"
      gap="$3"
    >
      <Spinner
        color="$nexaAccent"
        size="large"
      />
      <Paragraph color="$nexaSecondary">{label}</Paragraph>
    </YStack>
  )
}

export function AuthFrame({ children, ...props }: YStackProps) {
  return (
    <YStack
      flex={1}
      width="100%"
      justifyContent="center"
      alignItems="center"
      backgroundColor="$nexaCanvas"
      padding="$4"
      $md={{ padding: '$7' }}
      {...props}
    >
      <YStack
        width="100%"
        maxWidth={460}
        gap="$5"
      >
        <Brand />
        <Surface
          padding="$5"
          gap="$4"
        >
          {children}
        </Surface>
        <Paragraph
          textAlign="center"
          color="$nexaSecondary"
          fontSize={13}
          lineHeight={21}
        >
          Your health information deserves thoughtful care.
        </Paragraph>
      </YStack>
    </YStack>
  )
}

export function StatCard({
  title,
  label,
  value,
  unit,
  subtitle,
  description,
  tone = 'neutral',
  icon,
}: {
  title?: string
  label?: string
  value: string | number
  unit?: string
  subtitle?: string
  description?: string
  tone?: Tone
  icon?: ReactNode
}) {
  const displayTitle = label ?? title ?? ''
  const displaySubtitle = description ?? subtitle
  const selectedTone = tones[tone] ?? tones.neutral
  return (
    <Surface
      flex={1}
      minWidth={160}
      padding="$3.5"
      gap="$2"
      backgroundColor={selectedTone.backgroundColor}
      borderColor={tone === 'neutral' ? '$nexaBorder' : selectedTone.color}
    >
      <XStack
        justifyContent="space-between"
        alignItems="center"
      >
        <Text
          color="$nexaSecondary"
          fontSize={12}
          fontWeight="700"
          textTransform="uppercase"
          letterSpacing={0.5}
        >
          {displayTitle}
        </Text>
        {icon}
      </XStack>
      <XStack
        alignItems="baseline"
        gap="$1.5"
      >
        <Text
          color={tone === 'neutral' ? '$nexaText' : selectedTone.color}
          fontSize={26}
          fontWeight="800"
          lineHeight={32}
        >
          {value}
        </Text>
        {unit && (
          <Text
            color="$nexaSecondary"
            fontSize={14}
            fontWeight="600"
          >
            {unit}
          </Text>
        )}
      </XStack>
      {displaySubtitle && (
        <Text
          color="$nexaSecondary"
          fontSize={12}
          fontWeight="500"
        >
          {displaySubtitle}
        </Text>
      )}
    </Surface>
  )
}

export function TabPill({
  label,
  active,
  badge,
  icon,
  onPress,
}: {
  label: string
  active: boolean
  badge?: string | number
  icon?: ReactNode
  onPress: () => void
}) {
  return (
    <Button
      size="$3"
      borderRadius={10}
      borderWidth={1}
      backgroundColor={active ? '$nexaAccent' : '$nexaSurface'}
      borderColor={active ? '$nexaAccent' : '$nexaBorder'}
      hoverStyle={{
        backgroundColor: active ? '$nexaAccentHover' : '$nexaMuted',
        borderColor: '$nexaAccent',
      }}
      pressStyle={{
        backgroundColor: active ? '$nexaAccentHover' : '$nexaAccentSoft',
      }}
      onPress={onPress}
      paddingHorizontal="$3"
      height={40}
    >
      <XStack
        alignItems="center"
        gap="$2"
      >
        {icon}
        <Button.Text
          color={active ? '$nexaOnAccent' : '$nexaText'}
          fontWeight={active ? '700' : '600'}
          fontSize={14}
        >
          {label}
        </Button.Text>
        {badge !== undefined && (
          <XStack
            borderRadius={10}
            paddingHorizontal="$1.5"
            paddingVertical={2}
            backgroundColor={active ? 'rgba(255,255,255,0.25)' : '$nexaMuted'}
          >
            <Text
              color={active ? '$nexaOnAccent' : '$nexaSecondary'}
              fontSize={11}
              fontWeight="700"
            >
              {badge}
            </Text>
          </XStack>
        )}
      </XStack>
    </Button>
  )
}

export function NexaCardHeader({
  title,
  subtitle,
  badge,
  action,
}: {
  title: string
  subtitle?: string
  badge?: ReactNode
  action?: ReactNode
}) {
  return (
    <XStack
      justifyContent="space-between"
      alignItems="flex-start"
      flexWrap="wrap"
      gap="$2"
      width="100%"
    >
      <YStack
        gap="$1"
        flex={1}
        minWidth={160}
      >
        <XStack
          alignItems="center"
          gap="$2"
        >
          <Text
            color="$nexaText"
            fontSize={18}
            fontWeight="800"
            letterSpacing={-0.3}
          >
            {title}
          </Text>
          {badge}
        </XStack>
        {subtitle && (
          <Paragraph
            color="$nexaSecondary"
            fontSize={13}
            lineHeight={18}
          >
            {subtitle}
          </Paragraph>
        )}
      </YStack>
      {action}
    </XStack>
  )
}

