import { defineConfig } from '@playwright/test'

// UI-only checks use synthetic browser responses; no backend or device qualification.
export default defineConfig({
  testDir: './e2e',
  testMatch: 'product-polish.spec.ts',
  outputDir: '../../../.test-results/ui-product',
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.NEXA_UI_BASE_URL || 'http://127.0.0.1:3100',
    channel: process.env.NEXA_BROWSER_CHANNEL || 'chromium',
    screenshot: 'only-on-failure',
  },
})
