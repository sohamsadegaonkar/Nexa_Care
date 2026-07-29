import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = resolve(import.meta.dirname, '../../../..')
const WORKSPACE_FILES = [
  'packages/app/features/adjudication/AdjudicationQueueScreen.tsx',
  'packages/app/features/adjudication/AdjudicationReviewScreen.tsx',
  'packages/app/features/adjudication/AdjudicationResultScreen.tsx',
  'packages/app/features/adjudication/ProtectedSourceViewer.web.tsx',
  'packages/app/services/adjudicationWorkflowStore.ts',
]

function source(path: string): string {
  return readFileSync(resolve(ROOT, path), 'utf8')
}

describe('adjudication workspace security contract', () => {
  it.each(WORKSPACE_FILES)(
    '%s does not persist or navigate with protected session material',
    (path) => {
      const code = source(path)
      expect(code).not.toMatch(/\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b/)
      expect(code).not.toMatch(/[?&](?:review_session_id|reviewSessionId|consent_token)=/)
      expect(code).not.toMatch(/\bconsole\.(?:log|warn|error)\b/)
      expect(code).not.toMatch(/\bfetch\s*\(|\baxios\b/)
    }
  )

  it('keeps SOURCE_ONLY and QUARANTINE out of legacy review and commit screens', () => {
    for (const path of [
      'packages/app/features/pipeline/ReviewCockpitScreen.tsx',
      'packages/app/features/pipeline/CommitScreen.tsx',
    ]) {
      const code = source(path)
      expect(code).toContain("data.status === 'source_only'")
      expect(code).toContain("data.status === 'quarantined'")
      expect(code).toContain("router.replace('/doctor/pipeline/adjudication')")
    }
  })

  it('does not expose automatic approval or confidence in current adjudication screens', () => {
    const code = WORKSPACE_FILES.map(source).join('\n')
    expect(code).not.toContain('auto_approved')
    expect(code).not.toContain('100% confidence')
    expect(code).not.toContain('AI confidence 1.0')
    expect(code).not.toContain('commitExtractionJob')
  })
})
