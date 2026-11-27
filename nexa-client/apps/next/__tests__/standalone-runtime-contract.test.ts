import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { describe, expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const nextAppRoot = path.resolve(__dirname, '..')
const clientRoot = path.resolve(nextAppRoot, '../..')

describe('Next.js standalone runtime packaging contract', () => {
  it('configures standalone output and monorepo tracing root', () => {
    const nextConfig = require('../next.config.js') as {
      output?: string
      outputFileTracingRoot?: string
    }
    expect(nextConfig.output).toBe('standalone')
    expect(nextConfig.outputFileTracingRoot).toBe(clientRoot)
  })

  it('packages only standalone server, static assets, and public files as non-root', () => {
    const dockerfile = fs.readFileSync(
      path.join(clientRoot, 'Dockerfile.azure-staging'),
      'utf8'
    )
    expect(dockerfile).toContain('/opt/nexa/apps/next/.next/standalone')
    expect(dockerfile).toContain('/opt/nexa/apps/next/.next/static')
    expect(dockerfile).toContain('/opt/nexa/apps/next/public')
    expect(dockerfile).toContain('NEXT_PUBLIC_DEMO_MODE=false')
    expect(dockerfile).toContain('USER node')
    expect(dockerfile).toContain('EXPOSE 3000')
    expect(dockerfile).toContain('/opt/nexa/apps/next/server.js')
    expect(dockerfile).not.toMatch(/COPY --from=build[^\n]* \/opt\/nexa \/opt\/nexa\b/)
  })

  it('keeps next patched and excludes unused vercel CLI from runtime dependencies', () => {
    const pkg = JSON.parse(
      fs.readFileSync(path.join(nextAppRoot, 'package.json'), 'utf8')
    ) as { dependencies: Record<string, string> }
    expect(pkg.dependencies.vercel).toBeUndefined()
    expect(pkg.dependencies.next).toBe('16.4.6')
  })
})
