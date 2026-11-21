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
  await page.getByRole('button', { name: 'Sign In', exact: true }).click()
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

  // Secure server-owned bearer must never become JS-visible browser storage.
  const scriptVisibleCookies = await page.evaluate(() => document.cookie)
  expect(scriptVisibleCookies).not.toContain('nexa_provider_session')
  expect(await page.evaluate(() => localStorage.getItem('nexa_provider_session'))).toBeNull()
})
