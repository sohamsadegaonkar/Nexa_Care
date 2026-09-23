import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as SecureStore from 'expo-secure-store'
import {
  clearProviderAuthSession,
  getProviderAuthSnapshot,
  hydrateProviderAuthSession,
  storeProviderAuthSession,
  PROVIDER_ACCESS_TOKEN_STORAGE_KEY,
  PROVIDER_SESSION_INFO_STORAGE_KEY,
} from './providerAuthSession'

vi.mock('expo-secure-store', () => {
  const store = new Map<string, string>()
  return {
    getItemAsync: vi.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: vi.fn(async (key: string, val: string) => {
      store.set(key, val)
    }),
    deleteItemAsync: vi.fn(async (key: string) => {
      store.delete(key)
    }),
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 0,
  }
})

describe('providerAuthSession service', () => {
  beforeEach(async () => {
    await clearProviderAuthSession()
  })

  it('stores and retrieves a valid provider session securely', async () => {
    const futureDate = new Date(Date.now() + 3600 * 1000).toISOString()
    const info = {
      providerUid: '11111111-1111-4111-8111-111111111111',
      hospitalId: '22222222-2222-4222-8222-222222222222',
      expiresAt: futureDate,
    }

    await storeProviderAuthSession('test_provider_token_123', info)

    const snapshot = getProviderAuthSnapshot()
    expect(snapshot.status).toBe('authenticated')
    expect(snapshot.token).toBe('test_provider_token_123')
    expect(snapshot.session?.providerUid).toBe(info.providerUid)

    expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
      PROVIDER_ACCESS_TOKEN_STORAGE_KEY,
      'test_provider_token_123',
      expect.anything()
    )
  })

  it('clears provider session and wipes storage on logout', async () => {
    const futureDate = new Date(Date.now() + 3600 * 1000).toISOString()
    await storeProviderAuthSession('test_provider_token_123', {
      providerUid: '11111111-1111-4111-8111-111111111111',
      hospitalId: '22222222-2222-4222-8222-222222222222',
      expiresAt: futureDate,
    })

    await clearProviderAuthSession()

    const snapshot = getProviderAuthSnapshot()
    expect(snapshot.status).toBe('unauthenticated')
    expect(snapshot.token).toBeNull()
    expect(snapshot.session).toBeNull()

    expect(SecureStore.deleteItemAsync).toHaveBeenCalledWith(
      PROVIDER_ACCESS_TOKEN_STORAGE_KEY
    )
  })

  it('rejects storing empty or whitespace tokens', async () => {
    await expect(
      storeProviderAuthSession('', {
        providerUid: '111',
        hospitalId: '222',
        expiresAt: new Date().toISOString(),
      })
    ).rejects.toThrow('A valid provider access token is required.')
  })
})
