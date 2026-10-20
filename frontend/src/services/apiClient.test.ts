import { describe, expect, it } from 'vitest'
import { createApiClient } from './apiClient'

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
})
