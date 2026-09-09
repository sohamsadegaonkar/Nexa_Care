import { expect, test, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  // The verification build pins its production browser origin to this reserved
  // domain. Serve its assets from loopback without weakening the origin guard or
  // contacting a live API. API requests are intercepted separately below.
  const assetOrigin = process.env.NEXA_UI_ASSET_ORIGIN
  if (assetOrigin) {
    const target = new URL(assetOrigin)
    if (target.hostname !== '127.0.0.1' || target.protocol !== 'http:') {
      throw new Error('UI asset origin must be loopback HTTP')
    }
    await page.route('https://doctor.example.test/**', async (route) => {
      const original = new URL(route.request().url())
      const response = await route.fetch({
        url: `${target.origin}${original.pathname}${original.search}`,
      })
      await route.fulfill({ response })
    })
  }
})

async function stubSession(page: Page, authenticated: boolean) {
  await page.route('**/api/**', async (route) => {
    if (route.request().url().endsWith('/auth/web/session')) {
      await route.fulfill({
        status: authenticated ? 200 : 401,
        contentType: 'application/json',
        body: JSON.stringify(
          authenticated
            ? {
                authenticated: true,
                expires_at: '2099-01-01T00:00:00Z',
                provider_uid: 'synthetic-provider',
                hospital_id: '00000000-0000-4000-8000-000000000001',
                display_name: 'Synthetic Provider',
                hospital_name: 'Synthetic Care Organization',
                roles: ['clinician'],
              }
            : {}
        ),
      })
    } else {
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' })
    }
  })
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  )
}

test('provider credentials have accessible labels, a visible primary action and keyboard submission', async ({
  page,
}) => {
  await stubSession(page, false)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  await expect(page).toHaveTitle(/Nexa Care/)
  await expect(page.getByRole('heading', { name: 'Provider Login' })).toBeVisible()
  await page.getByLabel('Email or Login Identifier').fill('synthetic@example.test')
  await page.getByLabel('Password', { exact: true }).fill('synthetic-test-only')
  const button = page.getByRole('button', { name: 'Sign In', exact: true })
  await expect(button).toBeVisible()
  expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(48)
  await page.getByLabel('Password', { exact: true }).press('Enter')
  await expect(
    page.getByRole('alert').filter({ hasText: 'Invalid email or password.' })
  ).toBeVisible()
  expect(errors).toEqual([])
})

for (const width of [360, 768, 1440]) {
  test(`entry and workspace fit a ${width}px viewport`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    await stubSession(page, false)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Provider Login' })).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await page.screenshot({ path: testInfo.outputPath(`login-${width}.png`), fullPage: true })
    await page.unroute('**/api/**')
    await stubSession(page, true)
    await page.goto('/doctor/dashboard')
    await expect(page.getByRole('heading', { name: 'Ready for your next patient' })).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Provider navigation' })).toBeVisible()
    await expect(page.getByText('No pending requests')).toHaveCount(0)
    await expectNoHorizontalOverflow(page)
    expect(
      await page
        .getByRole('main')
        .evaluate((element) => element.scrollHeight <= element.clientHeight + 1)
    ).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`workspace-${width}.png`), fullPage: true })
    await page.getByRole('button', { name: /Change theme/ }).click()
    await expect(page.locator('html')).toHaveClass(/t_dark/)
    await expectNoHorizontalOverflow(page)
    await page.screenshot({
      path: testInfo.outputPath(`workspace-dark-${width}.png`),
      fullPage: true,
    })
  })
}

test('patient web entry explains the supported mobile path', async ({ page }) => {
  await stubSession(page, false)
  await page.goto('/')
  await page.getByRole('button', { name: 'Continue as Patient' }).click()
  await expect(page.getByRole('heading', { name: 'Your patient account' })).toBeVisible()
  await expect(page.getByText(/Continue in the Nexa Care mobile app/)).toBeVisible()
})
