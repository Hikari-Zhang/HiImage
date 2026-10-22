import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApiClient, ensureApiSuccess, readApiResponse } from './apiClient'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('apiClient', () => {
  it.each([
    [{ message: '模型尚未就绪', detail: 'longer detail' }, '模型尚未就绪'],
    [{ detail: '图像无法解码' }, '图像无法解码'],
    [{ detail: [{ loc: ['body', 'task_id'], msg: 'Field required', type: 'missing' }] }, 'task_id: Field required'],
    [{ detail: [{ loc: ['body', 'rois', 0], msg: 'invalid ROI' }, { loc: ['body', 'device'], msg: 'invalid device' }] }, 'rois.0: invalid ROI；device: invalid device'],
  ])('formats backend errors without stringifying objects: %j', async (body, message) => {
    const response = new Response(JSON.stringify(body), { status: 422 })
    const text = vi.spyOn(response, 'text')
    await expect(readApiResponse(response, '处理失败')).rejects.toMatchObject({ message, status: 422 })
    expect(text).toHaveBeenCalledOnce()
  })

  it.each([
    ['GPU unavailable', 'GPU unavailable'],
    ['<html><body>Bad Gateway</body></html>', '超分失败 (502)'],
    ['', '超分失败 (502)'],
    ['{"detail":', '{"detail":'],
  ])('reads a non-JSON error body exactly once: %s', async (body, message) => {
    const response = new Response(body, { status: 502 })
    const text = vi.spyOn(response, 'text')
    await expect(readApiResponse(response, '超分失败')).rejects.toMatchObject({ message, status: 502 })
    expect(text).toHaveBeenCalledOnce()
  })

  it('retains structured error metadata for ordinary API failures', async () => {
    const response = new Response(JSON.stringify({ message: 'model loading', code: 'MODEL_LOADING', retryable: true }), { status: 503 })
    await expect(readApiResponse(response)).rejects.toMatchObject({ status: 503, code: 'MODEL_LOADING', retryable: true })
  })

  it.each(['not JSON', '', '<html>index</html>'])('rejects a malformed successful JSON response: %s', async (body) => {
    const response = new Response(body)
    await expect(readApiResponse(response, '读取模型失败')).rejects.toMatchObject({
      status: 200, code: 'INVALID_RESPONSE', message: '读取模型失败：服务返回了无效的 JSON 响应',
    })
  })

  it('supports a 204 response and empty successful mutations', async () => {
    await expect(readApiResponse(new Response(null, { status: 204 }))).resolves.toBeUndefined()
    await expect(ensureApiSuccess(new Response(null, { status: 200 }))).resolves.toBeUndefined()
  })

  it('uses one read for successful JSON responses', async () => {
    const response = new Response(JSON.stringify({ image: 'result' }))
    const text = vi.spyOn(response, 'text')
    vi.stubGlobal('fetch', vi.fn(async () => response))
    await expect(createApiClient().json('/api/upscale')).resolves.toEqual({ image: 'result' })
    expect(text).toHaveBeenCalledOnce()
  })

  it('preserves abort errors without retrying or wrapping them', async () => {
    const error = new DOMException('aborted', 'AbortError')
    const fetch = vi.fn().mockRejectedValue(error)
    vi.stubGlobal('fetch', fetch)
    await expect(createApiClient().json('/api/models/list')).rejects.toBe(error)
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('handles a malformed queue-full response without reading it again', async () => {
    const response = new Response('<html>queue full</html>', { status: 429 })
    const text = vi.spyOn(response, 'text')
    vi.stubGlobal('fetch', vi.fn(async () => response))
    await expect(createApiClient().json('/api/upscale')).rejects.toMatchObject({
      status: 429, retryable: true, message: '服务器任务队列已满，请稍后重试',
    })
    expect(text).toHaveBeenCalledOnce()
  })
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
