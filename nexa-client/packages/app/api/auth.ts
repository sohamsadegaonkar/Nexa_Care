import { apiClient } from '../utils/apiClient'

export interface MergeChallengeResponse {
  challenge_token: string
  requires_mfa: boolean
  expires_in_seconds: number
}

export async function createMergeChallenge(): Promise<MergeChallengeResponse> {
  const res = await apiClient.post<MergeChallengeResponse>('/api/v2/auth/challenge/merge')
  return res.data
}

export async function verifyMergeChallenge(
  challengeToken: string,
  totpCode: string
): Promise<{ challenge_token: string; verified: boolean }> {
  const res = await apiClient.post<{ challenge_token: string; verified: boolean }>('/api/v2/auth/challenge/merge/verify', {
    challenge_token: challengeToken,
    totp_code: totpCode,
  })
  return res.data
}
