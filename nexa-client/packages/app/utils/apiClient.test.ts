import { afterEach, describe, expect, it, vi } from 'vitest'


async function loadApiBaseUrl(): Promise<string> {
  vi.resetModules()
  return (await import('./apiClient')).API_BASE_URL
}


describe('API base URL resolution', () => {
  afterEach(() => {
    delete process.env.EXPO_PUBLIC_API_URL
    delete process.env.NEXT_PUBLIC_API_URL
    vi.resetModules()
  })

  it('uses the Expo public URL for native bundles', async () => {
    process.env.EXPO_PUBLIC_API_URL = 'https://native.example.test/'
    process.env.NEXT_PUBLIC_API_URL = 'https://web.example.test'

    await expect(loadApiBaseUrl()).resolves.toBe('https://native.example.test')
  })

  it('falls back to the Next public URL for web', async () => {
    process.env.NEXT_PUBLIC_API_URL = 'https://web.example.test/'

    await expect(loadApiBaseUrl()).resolves.toBe('https://web.example.test')
  })

  it('stays empty when neither public URL is configured', async () => {
    await expect(loadApiBaseUrl()).resolves.toBe('')
  })

  it('fails before fetch with a specific error when no URL is configured', async () => {
    vi.resetModules()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const { apiClient } = await import('./apiClient')

    await expect(
      apiClient.post('/api/v2/auth/otp/send', { phone: '0000000000' }, { noAuth: true }),
    ).rejects.toMatchObject({ code: 'MISSING_API_BASE_URL', status: 0 })
    expect(fetchSpy).not.toHaveBeenCalled()
    fetchSpy.mockRestore()
  })
})
