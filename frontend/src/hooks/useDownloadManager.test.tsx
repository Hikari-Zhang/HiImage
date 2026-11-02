import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDownloadManager } from './useDownloadManager'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useBackendStore } from '../stores/useBackendStore'

vi.mock('../components/ui', () => ({ showToast: vi.fn() }))

class FakeEventSource {
  static CLOSED = 2
  static instances: FakeEventSource[] = []
  static get latest() { return this.instances[this.instances.length - 1]! }
  readyState = 1
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  listener?: (event: MessageEvent) => void
  constructor(public url: string) { FakeEventSource.instances.push(this) }
  addEventListener(_name: string, listener: (event: MessageEvent) => void) { this.listener = listener }
  close() { this.readyState = 2 }
  emit(downloadId: string, status: string) {
    this.listener?.({ data: JSON.stringify({ modelId: 'a', downloadId, status }) } as MessageEvent)
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
const response = (value: unknown) => new Response(JSON.stringify(value))

beforeEach(() => {
  useDownloadStore.setState({ tasks: {}, bulkSession: { active: false, modelIds: [] } })
  useBackendStore.getState().setBackendURL('')
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('download attempt isolation', () => {
  it('ignores late events and errors from a cancelled connection after retry', async () => {
    let attempt = 0
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
      if (init?.method === 'POST') return response({ modelId: 'a', downloadId: `attempt-${++attempt}`, status: 'downloading' })
      return response({ models: [], ok: true })
    }))
    const hook = renderHook(() => useDownloadManager())
    await act(async () => { await hook.result.current.startDownload('a') })
    const old = FakeEventSource.latest
    await act(async () => { await hook.result.current.cancelDownload('a'); await hook.result.current.startDownload('a') })
    const current = FakeEventSource.latest
    act(() => { old.emit('attempt-1', 'cancelled'); old.onerror?.() })
    expect(useDownloadStore.getState().getTask('a')?.status).toBe('downloading')
    expect(current.readyState).toBe(1)
    // 旧消息即使误到达当前连接，也不能覆盖新实例。
    act(() => { current.emit('attempt-1', 'done') })
    expect(useDownloadStore.getState().getTask('a')?.status).toBe('downloading')
    act(() => { current.emit('attempt-2', 'cancelled') })
  })

  it('orders POST, cancellation and retry even when the first POST returns late', async () => {
    const first = deferred<Response>()
    const methods: string[] = []
    const urls: string[] = []
    let attempt = 0
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
      if (!init?.method) return response({ models: [] })
      methods.push(init.method)
      urls.push(String(url))
      if (init.method === 'POST' && ++attempt === 1) return first.promise
      return response({ modelId: 'a', downloadId: 'second', status: 'downloading', ok: true })
    }))
    const hook = renderHook(() => useDownloadManager())
    let starting!: Promise<void>, cancelling!: Promise<void>, retrying!: Promise<void>
    act(() => { starting = hook.result.current.startDownload('a') })
    await waitFor(() => expect(methods).toEqual(['POST']))
    act(() => { cancelling = hook.result.current.cancelDownload('a'); retrying = hook.result.current.startDownload('a') })
    expect(methods).toEqual(['POST'])
    await act(async () => {
      first.resolve(response({ modelId: 'a', downloadId: 'first', status: 'downloading' }))
      await Promise.all([starting, cancelling, retrying])
    })
    expect(methods).toEqual(['POST', 'DELETE', 'POST'])
    expect(urls[1]).toContain('?download_id=first')
    expect(useDownloadStore.getState().getTask('a')?.downloadId).toBe('second')
    act(() => { FakeEventSource.latest.emit('second', 'cancelled') })
  })

  it('does not let a completed attempt health lookup overwrite a retry', async () => {
    const health = deferred<Response>()
    let attempt = 0
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
      if (String(url).includes('/health/')) return health.promise
      if (init?.method === 'POST') return response({ modelId: 'a', downloadId: `health-${++attempt}`, status: 'downloading' })
      return response({ models: [], ok: true })
    }))
    const hook = renderHook(() => useDownloadManager())
    await act(async () => { await hook.result.current.startDownload('a') })
    act(() => { FakeEventSource.latest.emit('health-1', 'done') })
    await act(async () => { await hook.result.current.startDownload('a') })
    await act(async () => { health.resolve(response({ status: 'ok' })); await health.promise })
    expect(useDownloadStore.getState().getTask('a')?.status).toBe('downloading')
    act(() => { FakeEventSource.latest.emit('health-2', 'cancelled') })
  })

  it('does not subscribe to an old attempt when a POST is still pending', async () => {
    const pending = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => init?.method === 'POST' ? pending.promise : response({ models: [] })))
    const hook = renderHook(() => useDownloadManager())
    let starting!: Promise<void>
    act(() => { starting = hook.result.current.startDownload('a') })
    await act(async () => { await hook.result.current.startDownload('a') })
    expect(FakeEventSource.instances).toHaveLength(0)
    await act(async () => {
      pending.resolve(response({ modelId: 'a', downloadId: 'pending', status: 'queued' }))
      await starting
    })
    expect(FakeEventSource.instances).toHaveLength(1)
    act(() => { FakeEventSource.latest.emit('pending', 'cancelled') })
  })

  it('ignores a stale bulk response after the model was cancelled and retried', async () => {
    const bulk = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
      if (String(url).endsWith('/bulk')) return bulk.promise
      if (init?.method === 'POST') return response({ modelId: 'a', downloadId: 'after-bulk', status: 'downloading' })
      return response({ models: [], ok: true })
    }))
    const hook = renderHook(() => useDownloadManager())
    let submitting!: Promise<void>
    act(() => { submitting = hook.result.current.startBulkDownload() })
    await act(async () => { await hook.result.current.cancelDownload('a'); await hook.result.current.startDownload('a') })
    await act(async () => {
      bulk.resolve(response({ submitted: 1, tasks: [{ modelId: 'a', downloadId: 'old-bulk', status: 'done' }] }))
      await submitting
    })
    expect(useDownloadStore.getState().getTask('a')?.downloadId).toBe('after-bulk')
    expect(useDownloadStore.getState().getTask('a')?.status).toBe('downloading')
    act(() => { FakeEventSource.latest.emit('after-bulk', 'cancelled') })
  })
})
