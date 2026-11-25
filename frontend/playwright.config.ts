import { defineConfig } from '@playwright/test'

const modelManager = process.env.HIIMAGE_TEST_MODEL_MANAGER === '1'
const fixtureServer = 'http://127.0.0.1:18787'

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: process.env.PLAYWRIGHT_CHANNEL || (process.platform === 'win32' ? 'msedge' : 'chromium'),
    headless: true,
  },
  webServer: [
    ...(modelManager ? [{
      command: 'node ../scripts/python.js frontend/e2e/model-manager-server.py',
      url: `${fixtureServer}/api/e2e-fixture`,
      reuseExistingServer: false,
    }] : []),
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173',
      env: modelManager ? { HIIMAGE_BACKEND_PROXY_TARGET: fixtureServer } : undefined,
      reuseExistingServer: !modelManager && !process.env.CI,
    },
  ],
})
