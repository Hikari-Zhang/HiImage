import { describe, expect, it } from 'vitest'
import { useSettingsStore } from './useSettingsStore'

describe('useSettingsStore.loadSettings', () => {
  it('throws a friendly error for a failed response', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '服务拒绝读取设置' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    })
    try {
      await expect(useSettingsStore.getState().loadSettings('')).rejects.toThrow('服务拒绝读取设置')
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
