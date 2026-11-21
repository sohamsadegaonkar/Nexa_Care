import { describe, expect, it } from 'vitest'
import { ProviderWebLoginStateSchema, ProviderWebAuthenticatedStateSchema } from './authNfcSchemas'

describe('real FastAPI provider web login response contract', () => {
  it('accepts MFA-required with expires_at:null (Pydantic datetime | None)', () => {
    expect(
      ProviderWebLoginStateSchema.parse({ status: 'mfa_required', expires_at: null })
    ).toEqual({ status: 'mfa_required', expires_at: null })
    expect(
      ProviderWebLoginStateSchema.parse({ status: 'mfa_required' })
    ).toEqual({ status: 'mfa_required' })
  })

  it('requires a nonempty expiry after authentication and rejects malformed states', () => {
    const completed = { status: 'authenticated', expires_at: '2099-01-01T00:00:00Z' }
    expect(ProviderWebLoginStateSchema.safeParse(completed).success).toBe(true)
    expect(ProviderWebAuthenticatedStateSchema.safeParse(completed).success).toBe(true)
    expect(ProviderWebLoginStateSchema.safeParse({ status: 'authenticated', expires_at: null }).success).toBe(false)
    expect(ProviderWebLoginStateSchema.safeParse({ status: 'authenticated' }).success).toBe(false)
    expect(ProviderWebLoginStateSchema.safeParse({ status: 'mfa_required', expires_at: '2099-01-01' }).success).toBe(false)
    expect(ProviderWebLoginStateSchema.safeParse({ status: 'mfa_required', expires_at: 3 }).success).toBe(false)
    expect(ProviderWebLoginStateSchema.safeParse({ status: 'unknown', expires_at: null }).success).toBe(false)
  })
})
