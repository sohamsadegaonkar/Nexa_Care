import { XStack, Text } from 'tamagui'

/**
 * RiskBadge — colour-coded risk level indicator.
 *
 * LOW_RISK    → green
 * MEDIUM_RISK → yellow / orange
 * HIGH_RISK   → red
 * CRITICAL    → deep red
 */

export type RiskLevel = 'LOW_RISK' | 'MEDIUM_RISK' | 'HIGH_RISK' | 'CRITICAL'

export interface RiskBadgeProps {
  level: RiskLevel
}

const RISK_STYLES = {
  LOW_RISK: { bg: '$green4', color: '$green10', label: 'Low' },
  MEDIUM_RISK: { bg: '$orange4', color: '$orange10', label: 'Medium' },
  HIGH_RISK: { bg: '$red4', color: '$red10', label: 'High' },
  CRITICAL: { bg: '$red6', color: '$red12', label: 'Critical' },
} as const satisfies Record<RiskLevel, { bg: string; color: string; label: string }>

export default function RiskBadge({ level }: RiskBadgeProps) {
  const style = RISK_STYLES[level] ?? RISK_STYLES.LOW_RISK

  return (
    <XStack
      backgroundColor={style.bg}
      borderRadius="$2"
      paddingHorizontal="$2"
      paddingVertical="$1"
      alignItems="center"
    >
      <Text
        color={style.color}
        fontSize="$1"
        fontWeight="700"
      >
        {style.label}
      </Text>
    </XStack>
  )
}
