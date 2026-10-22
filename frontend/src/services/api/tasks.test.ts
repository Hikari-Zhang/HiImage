import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApiClient } from '../apiClient'
import { createTaskAPI } from './tasks'

afterEach(() => vi.unstubAllGlobals())

describe('task status API', () => {
  it('reads a snapshot with an encoded ID and disables cached status', async () => {
    const snapshot = { task_id: 'job#1', status: 'running', progress: 40 }
    const fetch = vi.fn(async () => new Response(JSON.stringify(snapshot)))
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()
    await expect(createTaskAPI(createApiClient()).getTask('job#1', controller.signal)).resolves.toEqual(snapshot)
    expect(fetch).toHaveBeenCalledWith('/api/tasks/job%231', expect.objectContaining({ cache: 'no-store', signal: controller.signal }))
  })

  it('surfaces missing or expired records instead of returning them as running tasks', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: '任务不存在或记录已过期' }), { status: 404 })))
    await expect(createTaskAPI(createApiClient()).getTask('missing')).rejects.toMatchObject({ status: 404, message: '任务不存在或记录已过期' })
  })

  it('preserves cancellation without retrying the query', async () => {
    const error = new DOMException('cancelled', 'AbortError')
    const fetch = vi.fn(async () => { throw error })
    vi.stubGlobal('fetch', fetch)
    await expect(createTaskAPI(createApiClient()).getTask('one')).rejects.toBe(error)
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('propagates expiration from the result endpoint without retrying inference', async () => {
    const fetch = vi.fn(async () => new Response('{"detail":"结果已被清理"}', { status: 410 }))
    vi.stubGlobal('fetch', fetch)
    await expect(createTaskAPI(createApiClient()).getTaskResult('one')).rejects.toMatchObject({ status: 410 })
    expect(fetch).toHaveBeenCalledWith('/api/tasks/one/result', expect.objectContaining({ cache: 'no-store' }))
    expect(fetch).toHaveBeenCalledOnce()
  })
})
