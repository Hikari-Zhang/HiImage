import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApiClient } from './apiClient'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiClient', () => {
  it('uses same-origin by default and builds REST, SSE and websocket URLs', () => {
    const client = createApiClient()
    expect(client.url('/api/health')).toBe('/api/health')
    expect(client.sseUrl('/api/events')).toBe('/api/events')
    expect(client.websocketUrl('/api/progress')).toBe('ws://localhost:3000/api/progress')
  })

  it('normalizes an explicit base URL and upgrades websocket protocol', () => {
    const client = createApiClient('https://example.test/base/')
    expect(client.url('/api/health')).toBe('https://example.test/base/api/health')
    expect(client.sseUrl('/api/events')).toBe('https://example.test/base/api/events')
    expect(client.websocketUrl('/api/progress')).toBe('wss://example.test/base/api/progress')
  })

  it('turns a 429 into a typed retryable queue error without retrying', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'INFERENCE_QUEUE_FULL',
      message: 'backend message',
      retryable: true,
    }), {
      status: 429,
      headers: { 'Content-Type': 'application/json', 'Retry-After': '1' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const request = createApiClient().fetch('/api/upscale', { method: 'POST' })

    await expect(request).rejects.toMatchObject({
      message: '服务器任务队列已满，请稍后重试',
      status: 429,
      code: 'INFERENCE_QUEUE_FULL',
      retryable: true,
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
