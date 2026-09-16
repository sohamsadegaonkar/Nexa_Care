import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get, post, del } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
}))

vi.mock('../utils/apiClient', () => ({
  apiClient: { get, post, delete: del },
}))

import {
  disablePhoneDiscoverability,
  enablePhoneDiscoverability,
  getPhoneDiscoverability,
} from './patientPhoneDiscoverability'

describe('patient phone discoverability transport', () => {
  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    del.mockReset()
  })

  it('reads only the boolean state', async () => {
    get.mockResolvedValueOnce({ data: { enabled: false } })
    await expect(getPhoneDiscoverability()).resolves.toEqual({ enabled: false })
    expect(get).toHaveBeenCalledWith('/api/v2/patient/me/discoverability/phone')
  })

  it('submits phone and OTP only in the authenticated POST body', async () => {
    post.mockResolvedValueOnce({ data: { enabled: true } })
    await expect(
      enablePhoneDiscoverability('+918000000001', '123456')
    ).resolves.toEqual({ enabled: true })
    expect(post).toHaveBeenCalledWith(
      '/api/v2/patient/me/discoverability/phone/enable',
      { phone: '+918000000001', otp: '123456' }
    )
    expect(post.mock.calls[0]?.[0]).not.toContain('+918000000001')
    expect(post.mock.calls[0]?.[0]).not.toContain('123456')
  })

  it('disables without transmitting the phone value', async () => {
    del.mockResolvedValueOnce({ data: { enabled: false } })
    await expect(disablePhoneDiscoverability()).resolves.toEqual({ enabled: false })
    expect(del).toHaveBeenCalledWith('/api/v2/patient/me/discoverability/phone')
  })
})
