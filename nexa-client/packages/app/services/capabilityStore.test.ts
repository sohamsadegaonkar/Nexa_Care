import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearAllCapabilities,
  getCapability,
  setCapability,
  type CapabilityGrant,
} from './capabilityStore'

function grant(
  workflowId: string,
  token: string,
  expiresAt = '2099-01-01T00:00:00.000Z'
): CapabilityGrant {
  return {
    workflowId,
    patientId: `patient-${workflowId}`,
    token,
    purpose: 'clinical_view',
    scope: ['clinical'],
    expiresAt,
  }
}

describe('in-memory capability isolation', () => {
  afterEach(() => {
    clearAllCapabilities()
    vi.useRealTimers()
  })

  it('does not let one workflow read another workflow grant', () => {
    setCapability(grant('workflow-a', 'secret-a'))
    setCapability(grant('workflow-b', 'secret-b'))

    expect(getCapability('workflow-a')?.token).toBe('secret-a')
    expect(getCapability('workflow-b')?.token).toBe('secret-b')
    expect(getCapability('workflow-c')).toBeNull()
  })

  it('removes expired capabilities', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-21T00:00:00.000Z'))
    setCapability(grant('expired', 'expired-secret', '2026-07-20T23:59:59.000Z'))

    expect(getCapability('expired')).toBeNull()
  })

  it('clears every capability on logout cleanup', () => {
    setCapability(grant('workflow-a', 'secret-a'))
    setCapability(grant('workflow-b', 'secret-b'))
    clearAllCapabilities()

    expect(getCapability('workflow-a')).toBeNull()
    expect(getCapability('workflow-b')).toBeNull()
  })
})
