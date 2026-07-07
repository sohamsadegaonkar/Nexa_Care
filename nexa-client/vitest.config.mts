import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    include: ['apps/next/__tests__/**/*.test.ts'],
    exclude: ['**/node_modules/**', '**/dist/**', '**/.next/**'],
    // These tests spawn Next.js dev/build processes; run files serially to avoid
    // resource contention and port startup timeouts in CI.
    fileParallelism: false,
  },
})
