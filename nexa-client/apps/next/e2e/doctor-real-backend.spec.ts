import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'

const demoEmail = 'demo.doctor@nexacare.in'

function currentSyntheticTotp() {
  // CI-only seed; the short-lived TOTP is not written to logs or artifacts.
  return execFileSync(
    'python',
    ['-c', 'import os, pyotp; print(pyotp.TOTP(os.environ["DEMO_PROVIDER_MFA_SECRET"]).now())'],
    { encoding: 'utf8' }
  ).trim()
}

test('real clinician web session uses Next same-origin proxy and synthetic MFA', async ({ page }) => {
  const password = process.env.DEMO_PROVIDER_PASSWORD
  expect(password, 'ephemeral CI-only password must be configured').toBeTruthy()

  await page.goto('/doctor/login', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: 'Provider Login' })).toBeVisible()

  // No page.route(), HAR fixture or response mock: both calls reach real FastAPI.
  const denied = await page.evaluate(async () => {
    const response = await fetch('/api/v2/auth/web/session', { credentials: 'include' })
    return {
      status: response.status,
      body: await response.json(),
      xssHeader: response.headers.get('x-xss-protection'),
    }
  })
  expect(denied.status).toBe(401)
  expect(denied.body).toHaveProperty('detail')
  expect(denied.xssHeader).toBe('1; mode=block')

  await page.getByLabel('Email or Login Identifier').fill(demoEmail)
  await page.getByLabel('Password', { exact: true }).fill(password!)
  const loginResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith('/api/v2/auth/web/login') &&
      response.request().method() === 'POST',
    { timeout: 20_000 }
  )
  await page.getByRole('button', { name: 'Sign In', exact: true }).click()
  const loginResponse = await loginResponsePromise
  // Only status is logged; never request body, Set-Cookie, password, MFA code or tokens.
  console.log(`SYNTHETIC_DOCTOR_LOGIN_HTTP=${loginResponse.status()}`)
  expect(loginResponse.status()).toBe(200)
  const loginState = await loginResponse.json()
  expect(loginState.status).toBe('mfa_required')
  await expect(page.getByRole('heading', { name: 'Verify Provider' })).toBeVisible()

  await page.getByLabel('Authenticator Code').fill(currentSyntheticTotp())
  await page.getByRole('button', { name: 'Verify', exact: true }).click()
  await expect(page).toHaveURL(/\/doctor\/dashboard/, { timeout: 35_000 })
  await expect(page.getByRole('heading', { name: 'Ready for your next patient' })).toBeVisible()

  const session = await page.evaluate(async () => {
    const response = await fetch('/api/v2/auth/web/session', { credentials: 'include' })
    const body = await response.json()
    return {
      status: response.status,
      provider: typeof body.provider_uid === 'string',
      hospital: typeof body.hospital_id === 'string',
    }
  })
  expect(session).toEqual({ status: 200, provider: true, hospital: true })

  // The signed ORIGINAL DOCUMENT endpoint must remain closed to a fully
  // authenticated doctor who has no independent patient-history grant.
  // Use the *real* cookie session, valid CSRF and synthetic header IDs, so this
  // does not pass merely by triggering the anonymous-auth or CSRF guard.
  const originalDenials = await page.evaluate(async () => {
    const current = await fetch('/api/v2/auth/web/session', { credentials: 'include' })
    if (!current.ok) throw new Error('No live provider session for consent-denial test')
    const provider = await current.json() as { hospital_id: string }
    const csrfPart = document.cookie
      .split('; ')
      .find((part) => part.startsWith('nexa_csrf='))
      ?.split('=')[1]
    if (!csrfPart) throw new Error('No browser CSRF cookie for consent-denial test')
    const headers: Record<string, string> = {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'X-CSRF-Token': decodeURIComponent(csrfPart),
      'X-Hospital-Id': provider.hospital_id,
      'X-Patient-Id': crypto.randomUUID(),
    }
    const requests = [
      { operation: 'request', body: { document_id: crypto.randomUUID() } },
      { operation: 'status', body: { request_id: crypto.randomUUID() } },
      { operation: 'open', body: { request_id: crypto.randomUUID() } },
    ]
    return Promise.all(requests.map(async ({ operation, body }) => {
      // An absent token on "request" and a forged token on "status"/"open"
      // must fail before a database row can be accessed or document bytes sent.
      const attempt = await fetch('/api/v2/original-access/v1/' + operation, {
        method: 'POST',
        credentials: 'include',
        headers: operation === 'request'
          ? headers
          : { ...headers, 'X-Consent-Token': 'synthetic-fabricated-not-a-capability' },
        body: JSON.stringify(body),
      })
      const raw = await attempt.text()
      const parsed = JSON.parse(raw) as { detail?: unknown }
      return {
        operation,
        status: attempt.status,
        detail: typeof parsed.detail === 'string' ? parsed.detail : null,
        cacheControl: attempt.headers.get('cache-control') ?? '',
        contentType: attempt.headers.get('content-type') ?? '',
        noPdf: !raw.startsWith('%PDF-'),
      }
    }))
  })
  for (const attempt of originalDenials) {
    expect(attempt.status, attempt.operation).toBe(403)
    expect(attempt.detail, attempt.operation).toContain('consent token required')
    expect(attempt.cacheControl, attempt.operation).toContain('no-store')
    expect(attempt.contentType, attempt.operation).toContain('application/json')
    expect(attempt.noPdf, attempt.operation).toBe(true)
  }
  console.log('REAL_BACKEND_ORIGINAL_UNGRANTED=DENIED_THREE_OPERATIONS')

  // Secure server-owned bearer must never become JS-visible browser storage.
  const scriptVisibleCookies = await page.evaluate(() => document.cookie)
  expect(scriptVisibleCookies).not.toContain('nexa_provider_session')
  expect(await page.evaluate(() => localStorage.getItem('nexa_provider_session'))).toBeNull()
})
