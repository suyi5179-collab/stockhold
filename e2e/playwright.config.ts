import { defineConfig } from '@playwright/test'

// Expects ./scripts/demo.sh to be running (Flask on :8840, LocalStack, Lambdas).
export default defineConfig({
  testDir: '.',
  timeout: 240_000,
  expect: { timeout: 90_000 },
  use: { baseURL: process.env.STOCKHOLD_URL || 'http://127.0.0.1:8840', headless: true, viewport: { width: 1440, height: 900 } },
  reporter: [['list']],
  workers: 1,
})
