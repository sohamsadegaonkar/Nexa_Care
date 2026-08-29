import { createRequire } from 'node:module'
import { afterEach, describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const nextConfig = require('../next.config.js') as {
  rewrites: () => Promise<Array<{ source: string; destination: string }>>
}

describe('Next same-origin API proxy configuration', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('keeps browser API paths on the doctor origin while proxying server-side', async () => {
    vi.stubEnv('API_PROXY_TARGET', 'https://api.example.test/')

    await expect(nextConfig.rewrites()).resolves.toEqual([
      {
        source: '/api/:path*',
        destination: 'https://api.example.test/api/:path*',
      },
    ])
    expect(JSON.stringify(nextConfig)).not.toContain('api.example.test')
  })

  it.each([
    'not a URL',
    'https://user:password@api.example.test',
    'https://api.example.test?redirect=elsewhere',
    'https://api.example.test/base-path',
  ])('rejects malformed server-only proxy target %s', async (target) => {
    vi.stubEnv('API_PROXY_TARGET', target)
    await expect(nextConfig.rewrites()).rejects.toThrow('API_PROXY_TARGET')
  })
})
