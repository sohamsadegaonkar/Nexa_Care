import { describe, expect, it } from 'vitest'
import { type RuntimeConfigError, resolveApiUrl } from './runtimeConfig'

describe('runtime API URL policy', () => {
  it('rejects a missing Expo API URL', () => {
    expect(() =>
      resolveApiUrl(undefined, {
        environment: 'development',
        allowHttp: true,
        source: 'expo',
      })
    ).toThrowError(
      expect.objectContaining<Partial<RuntimeConfigError>>({ code: 'MISSING_API_BASE_URL' })
    )
  })

  it('normalizes trailing slashes', () => {
    expect(
      resolveApiUrl('https://api.example.test///', {
        environment: 'preview',
        allowHttp: false,
        source: 'expo',
      })
    ).toBe('https://api.example.test')
  })

  it('permits explicitly enabled HTTP in development', () => {
    expect(
      resolveApiUrl('http://192.0.2.10:8000/', {
        environment: 'development',
        allowHttp: true,
        source: 'expo',
      })
    ).toBe('http://192.0.2.10:8000')
  })

  it('rejects HTTP when development has not opted in', () => {
    expect(() =>
      resolveApiUrl('http://192.0.2.10:8000', {
        environment: 'development',
        allowHttp: false,
        source: 'expo',
      })
    ).toThrowError(
      expect.objectContaining<Partial<RuntimeConfigError>>({ code: 'INSECURE_API_URL' })
    )
  })

  it.each([
    'http://api.example.test',
    ['https:', '', 'localhost:8000'].join('/'),
    'https://127.0.0.1:8000',
    'https://192.168.1.20:8000',
  ])('rejects insecure or local production URL %s', (url) => {
    expect(() =>
      resolveApiUrl(url, {
        environment: 'production',
        allowHttp: false,
        source: 'expo',
      })
    ).toThrowError(
      expect.objectContaining<Partial<RuntimeConfigError>>({ code: 'INSECURE_API_URL' })
    )
  })
})
