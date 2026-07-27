import { XStack, Text } from 'tamagui'

/**
 * SourceBadge — provenance indicator for timeline events.
 *
 * Green for manual entry ("Manual entry"), blue for AI-extracted
 * with confidence percentage ("AI-extracted, 91% confidence").
 */

export interface SourceBadgeProps {
  /** 'manual' for human-entered data, 'ai_extracted' for AI-parsed */
  source: 'manual' | 'ai_extracted'
  /** Confidence score 0–100 (only meaningful when source='ai_extracted') */
  confidence?: number
}

export default function SourceBadge({ source, confidence }: SourceBadgeProps) {
  if (source === 'manual') {
    return (
      <XStack
        bg="$green4"
        br="$2"
        px="$2"
        py="$1"
        ai="center"
        gap="$1"
      >
        <Text
          col="$green10"
          size="$1"
          fontWeight="600"
        >
          Manual entry
        </Text>
      </XStack>
    )
  }

  // AI-extracted — always show confidence if available
  const label =
    confidence != null ? `AI-extracted, ${Math.round(confidence)}% confidence` : 'AI-extracted'

  return (
    <XStack
      bg="$blue4"
      br="$2"
      px="$2"
      py="$1"
      ai="center"
      gap="$1"
    >
      <Text
        col="$blue10"
        size="$1"
        fontWeight="600"
      >
        {label}
      </Text>
    </XStack>
  )
}
