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

const RISK_STYLES: Record<RiskLevel, { bg: string; color: string; label: string }> = {
  LOW_RISK: { bg: '$green4', color: '$green10', label: 'Low' },
  MEDIUM_RISK: { bg: '$orange4', color: '$orange10', label: 'Medium' },
  HIGH_RISK: { bg: '$red4', color: '$red10', label: 'High' },
  CRITICAL: { bg: '$red6', color: '$red12', label: 'Critical' },
}

export default function RiskBadge({ level }: RiskBadgeProps) {
  const style = RISK_STYLES[level] ?? RISK_STYLES.LOW_RISK

  return (
    <XStack
      bg={style.bg}
      br="$2"
      px="$2"
      py="$1"
      ai="center"
    >
      <Text col={style.color} size="$1" fontWeight="700">
        {style.label}
      </Text>
    </XStack>
  )
}
