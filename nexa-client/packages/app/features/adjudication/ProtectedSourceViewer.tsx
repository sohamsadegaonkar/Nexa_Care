import { Paragraph, YStack } from '@my/ui'

interface ProtectedSourceViewerProps {
  caseId: string
  reviewSessionId: string
  onTerminalAccessFailure: () => void
}

export function ProtectedSourceViewer(_props: ProtectedSourceViewerProps) {
  return (
    <YStack padding="$4">
      <Paragraph>
        The clinician source workspace is available only in the provider web app.
      </Paragraph>
    </YStack>
  )
}
