import { describe, expect, it } from 'vitest'
import { saveSettingsRequest } from './settingsService'

describe('saveSettingsRequest', () => {
  it('throws the backend error when the response is not successful', async () => {
    const client = {
      fetch: async () => new Response(JSON.stringify({ detail: '设备配置无效' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    }

    await expect(saveSettingsRequest(client, { device: 'bad' })).rejects.toThrow('设备配置无效')
  })
})
