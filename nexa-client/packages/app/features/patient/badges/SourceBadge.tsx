import { XStack, Text } from 'tamagui'

/**
 * SourceBadge — provenance indicator for records and timeline events.
 *
 * Distinct visual badges for each provenance source:
 * - 'clinician_recorded': Green badge ("Clinician recorded", or with hospital name)
 * - 'patient_uploaded': Purple badge ("Patient uploaded")
 * - 'patient_corrected': Orange badge ("Patient corrected")
 * - 'document_extracted': Blue badge ("Document extracted")
 * - 'ai_extracted': Blue badge ("AI-extracted, X% confidence")
 * - 'manual': Neutral/green badge ("Manual entry")
 */

export interface SourceBadgeProps {
  /** Provenance source identifier */
  source:
    | 'clinician_recorded'
    | 'patient_uploaded'
    | 'document_extracted'
    | 'patient_corrected'
    | 'manual'
    | 'ai_extracted'
    | (string & {})
  /** Confidence score 0–100 (only meaningful when source='ai_extracted' or 'document_extracted') */
  confidence?: number
  /** Facility or hospital name for clinician recorded records */
  hospitalName?: string
}

export default function SourceBadge({
  source,
  confidence,
  hospitalName,
}: SourceBadgeProps) {
  if (source === 'clinician_recorded') {
    const label = hospitalName
      ? `Clinician recorded • ${hospitalName}`
      : 'Clinician recorded'
    return (
      <XStack
        backgroundColor="$green4"
        borderRadius="$2"
        paddingHorizontal="$2"
        paddingVertical="$1"
        alignItems="center"
        gap="$1"
      >
        <Text color="$green10" fontSize="$1" fontWeight="600">
          {label}
        </Text>
      </XStack>
    )
  }

  if (source === 'patient_uploaded') {
    return (
      <XStack
        backgroundColor="$purple4"
        borderRadius="$2"
        paddingHorizontal="$2"
        paddingVertical="$1"
        alignItems="center"
        gap="$1"
      >
        <Text color="$purple10" fontSize="$1" fontWeight="600">
          Patient uploaded
        </Text>
      </XStack>
    )
  }

  if (source === 'patient_corrected') {
    return (
      <XStack
        backgroundColor="$orange4"
        borderRadius="$2"
        paddingHorizontal="$2"
        paddingVertical="$1"
        alignItems="center"
        gap="$1"
      >
        <Text color="$orange10" fontSize="$1" fontWeight="600">
          Patient corrected
        </Text>
      </XStack>
    )
  }

  if (source === 'manual') {
    return (
      <XStack
        backgroundColor="$green4"
        borderRadius="$2"
        paddingHorizontal="$2"
        paddingVertical="$1"
        alignItems="center"
        gap="$1"
      >
        <Text color="$green10" fontSize="$1" fontWeight="600">
          Manual entry
        </Text>
      </XStack>
    )
  }

  if (source === 'document_extracted') {
    const label =
      confidence != null
        ? `Document extracted (${Math.round(confidence)}%)`
        : 'Document extracted'
    return (
      <XStack
        backgroundColor="$blue4"
        borderRadius="$2"
        paddingHorizontal="$2"
        paddingVertical="$1"
        alignItems="center"
        gap="$1"
      >
        <Text color="$blue10" fontSize="$1" fontWeight="600">
          {label}
        </Text>
      </XStack>
    )
  }

  // AI-extracted — always show confidence if available
  const label =
    confidence != null
      ? `AI-extracted, ${Math.round(confidence)}% confidence`
      : 'AI-extracted'

  return (
    <XStack
      backgroundColor="$blue4"
      borderRadius="$2"
      paddingHorizontal="$2"
      paddingVertical="$1"
      alignItems="center"
      gap="$1"
    >
      <Text color="$blue10" fontSize="$1" fontWeight="600">
        {label}
      </Text>
    </XStack>
  )
}
