import { defineConfig } from '@playwright/test'

// Real CI-local FastAPI, PostgreSQL and Redis, not the mocked investor UI smoke.
// No owner laptop, phone, real clinical grant, NFC or production deployment.
export default defineConfig({
  testDir: './e2e',
  testMatch: 'doctor-real-backend.spec.ts',
  timeout: 120_000,
  expect: { timeout: 35_000 },
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:3000',
    browserName: 'chromium',
    channel: 'chromium',
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
})
