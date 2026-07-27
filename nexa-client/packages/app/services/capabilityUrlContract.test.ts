import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repo = resolve(import.meta.dirname, '../../..')
const routeFiles = [
  'apps/next/app/patient/[id]/page.tsx',
  'packages/app/features/scanner/screen.tsx',
  'packages/app/features/emergency/SearchScreen.tsx',
  'packages/app/features/pipeline/PipelineUploadScreen.tsx',
  'packages/app/features/pipeline/JobStatusScreen.tsx',
  'packages/app/features/pipeline/ReviewQueueScreen.tsx',
  'packages/app/features/pipeline/ReviewCockpitScreen.tsx',
  'packages/app/features/pipeline/CommitScreen.tsx',
]

describe('capability URL contract', () => {
  it.each(routeFiles)('%s never reads or constructs a bearer-token URL', (relativePath) => {
    const source = readFileSync(resolve(repo, relativePath), 'utf8')
    expect(source).not.toMatch(/[?&](?:consentToken|consent_token|capability|accessGrant)=/)
    expect(source).not.toMatch(
      /searchParams\.get\(['"](?:consentToken|consent_token|capability|accessGrant)['"]\)/
    )
  })

  it('passes only patientId and workflowId from the Next patient route', () => {
    const source = readFileSync(resolve(repo, routeFiles[0]), 'utf8')
    expect(source).toContain("searchParams.get('workflow_id')")
    expect(source).toContain('patientId={params.id}')
    expect(source).toContain('workflowId={workflowId}')
  })

  it.each(routeFiles.slice(3))(
    '%s resolves the token from the capability store',
    (relativePath) => {
      const source = readFileSync(resolve(repo, relativePath), 'utf8')
      expect(source).toMatch(/useCapability\(workflowId\)/)
      expect(source).toContain('Access session expired')
    }
  )
})
