import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    include: ['apps/next/__tests__/**/*.test.ts'],
    exclude: ['**/node_modules/**', '**/dist/**', '**/.next/**'],
  },
})
