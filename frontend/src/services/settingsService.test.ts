import { describe, expect, it } from 'vitest'
import { clearHfTokenRequest, saveSettingsRequest } from './settingsService'

describe('saveSettingsRequest', () => {
  it('preserves a plain-text server error instead of consuming the body twice', async () => {
    const client = { fetch: async () => new Response('磁盘空间不足', { status: 500 }) }
    await expect(saveSettingsRequest(client, {})).rejects.toThrow('磁盘空间不足')
  })
  it('throws the backend error when the response is not successful', async () => {
    const client = {
      fetch: async () => new Response(JSON.stringify({ detail: '设备配置无效' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    }

    await expect(saveSettingsRequest(client, { device: 'bad' })).rejects.toThrow('设备配置无效')
  })

  it('uses the explicit clear flag when deleting a configured token', async () => {
    let body = ''
    const client = {
      fetch: async (_path: string, init?: RequestInit) => {
        body = String(init?.body)
        return new Response(JSON.stringify({ status: 'ok' }))
      },
    }

    await clearHfTokenRequest(client)

    expect(JSON.parse(body)).toEqual({ clear_hf_token: true })
  })
})
