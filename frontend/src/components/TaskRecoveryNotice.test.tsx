import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import TaskRecoveryNotice from './TaskRecoveryNotice'
import { rememberPendingTask, readPendingTasks } from '../services/taskRecoveryStorage'
import { useProcessStore } from '../stores/useProcessStore'
import { downloadRecoveredResult } from '../services/recoveredResult'

vi.mock('../services/recoveredResult', () => ({ downloadRecoveredResult: vi.fn(async () => {}) }))

function remember(taskId = 'one', backendURL = '') {
  rememberPendingTask({ taskId, backendURL, sessionId: 'upscale', createdAt: Date.now() })
}
function response(status: string, taskId = 'one') {
  return new Response(JSON.stringify({ task_id: taskId, status, progress: 40 }))
}

beforeEach(() => { sessionStorage.clear(); useProcessStore.setState({ tasks: {}, sessions: {} }) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); sessionStorage.clear() })

describe('task recovery notice', () => {
  it('does nothing without a previous task and never submits inference', () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    render(<TaskRecoveryNotice backendURL="" />)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([
    ['queued', '后台正在排队'], ['running', '后台仍在处理（40%）'],
    ['succeeded', '后台处理已完成，但刷新前的图片结果无法取回'],
    ['failed', '后台任务失败'], ['interrupted', '请求已中断'],
  ])('shows recovered %s status without restoring or changing live tasks', async (status, expected) => {
    remember()
    const fetch = vi.fn(async () => response(status))
    vi.stubGlobal('fetch', fetch)
    render(<TaskRecoveryNotice backendURL="" />)
    expect(await screen.findByText(new RegExp(expected.replace(/[（）]/g, '\\$&')))).toBeInTheDocument()
    expect(useProcessStore.getState().tasks).toEqual({})
    expect(fetch).toHaveBeenCalledWith('/api/tasks/one', expect.objectContaining({ cache: 'no-store' }))
    expect(fetch.mock.calls).toHaveLength(1)
  })

  it('distinguishes unknown records from failure and allows dismissing without cancellation', async () => {
    remember()
    const fetch = vi.fn(async () => new Response('{"detail":"missing"}', { status: 404 }))
    vi.stubGlobal('fetch', fetch)
    render(<TaskRecoveryNotice backendURL="" />)
    expect(await screen.findByText(/无法确认任务是否完成/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '移除任务提示 one' }))
    expect(readPendingTasks()).toEqual([])
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('allows a failed query to be retried explicitly', async () => {
    remember()
    const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(response('succeeded'))
    vi.stubGlobal('fetch', fetch)
    render(<TaskRecoveryNotice backendURL="" />)
    expect(await screen.findByText(/暂时无法查询/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '刷新任务状态' }))
    expect(await screen.findByText(/后台处理已完成/)).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('ignores a mismatched task response instead of declaring success', async () => {
    remember()
    vi.stubGlobal('fetch', vi.fn(async () => response('succeeded', 'different-task')))
    render(<TaskRecoveryNotice backendURL="" />)
    expect(await screen.findByText(/暂时无法查询/)).toBeInTheDocument()
    expect(screen.queryByText(/后台处理已完成/)).not.toBeInTheDocument()
  })

  it('aborts stale queries on backend changes and keeps backend records isolated', async () => {
    remember('one')
    remember('other', 'https://other')
    let finish!: (response: Response) => void
    let firstSignal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (url.startsWith('https://other')) return Promise.resolve(response('failed', 'other'))
      firstSignal = init!.signal as AbortSignal
      return new Promise<Response>((resolve) => { finish = resolve })
    }))
    const view = render(<TaskRecoveryNotice backendURL="" />)
    view.rerender(<TaskRecoveryNotice backendURL="https://other/" />)
    expect(firstSignal.aborted).toBe(true)
    expect(await screen.findByText(/后台任务失败/)).toBeInTheDocument()
    await act(async () => { finish(response('succeeded')) })
    expect(screen.queryByText(/后台处理已完成/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '移除任务提示 other' }))
    expect(readPendingTasks().map((item) => item.taskId)).toEqual(['one'])
  })

  it('does not query a task still running in the current application', () => {
    useProcessStore.getState().startProcess('upscale', 'model', 'live')
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    render(<TaskRecoveryNotice backendURL="" />)
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })

  it('survives StrictMode setup cleanup and aborts requests on unmount', async () => {
    remember()
    const signals: AbortSignal[] = []
    vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => {
      const signal = init!.signal as AbortSignal
      signals.push(signal)
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
      })
    }))
    const view = render(<StrictMode><TaskRecoveryNotice backendURL="" /></StrictMode>)
    expect(signals).toHaveLength(2)
    expect(signals[0].aborted).toBe(true)
    view.unmount()
    await act(async () => {})
    expect(signals[1].aborted).toBe(true)
  })

  it('unlocks manual retry after a timed out query', async () => {
    vi.useFakeTimers()
    remember()
    vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
    })))
    render(<TaskRecoveryNotice backendURL="" />)
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(screen.getByText(/查询超时/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新任务状态' })).toBeEnabled()
    cleanup()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('fetches a cached result on click and prevents duplicate downloads', async () => {
    remember()
    let finish!: (value: Response) => void
    const fetch = vi.fn((url: string) => url.endsWith('/result')
      ? new Promise<Response>((resolve) => { finish = resolve })
      : Promise.resolve(new Response(JSON.stringify({ task_id: 'one', status: 'succeeded', result_available: true }))))
    vi.stubGlobal('fetch', fetch)
    render(<TaskRecoveryNotice backendURL="" />)
    const button = await screen.findByRole('button', { name: '下载任务结果 one' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(button).toBeDisabled()
    await act(async () => { finish(new Response('{"image":"cached"}')) })
    expect(downloadRecoveredResult).toHaveBeenCalledWith({ image: 'cached' }, expect.any(AbortSignal))
    expect(await screen.findByText(/已发起结果下载/)).toBeInTheDocument()
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/tasks/one', '/api/tasks/one/result'])
    expect(useProcessStore.getState().tasks).toEqual({})
  })

  it('can recover a completed task after the in-memory application state is lost', async () => {
    const process = useProcessStore.getState()
    process.startProcess('upscale', 'model', 'completed')
    process.finishProcess('completed')
    useProcessStore.setState({ tasks: {}, sessions: {} })
    const fetch = vi.fn(async () => new Response(JSON.stringify({ task_id: 'completed', status: 'succeeded', result_available: true })))
    vi.stubGlobal('fetch', fetch)
    render(<TaskRecoveryNotice backendURL="" />)
    expect(await screen.findByRole('button', { name: '下载任务结果 completed' })).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/tasks/completed', expect.objectContaining({ cache: 'no-store' }))
    expect(useProcessStore.getState().tasks).toEqual({})
  })

  it('handles a cache evicted between status lookup and download', async () => {
    remember()
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/result')
      ? new Response('{"detail":"gone"}', { status: 410 })
      : new Response(JSON.stringify({ task_id: 'one', status: 'succeeded', result_available: true }))))
    render(<TaskRecoveryNotice backendURL="" />)
    fireEvent.click(await screen.findByRole('button', { name: '下载任务结果 one' }))
    expect(await screen.findByText(/暂存结果已过期或被清理/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '下载任务结果 one' })).not.toBeInTheDocument()
    expect(downloadRecoveredResult).not.toHaveBeenCalled()
  })

  it('aborts a result download on backend changes and never saves its late response', async () => {
    remember()
    let finish!: (value: Response) => void
    let signal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (!url.endsWith('/result')) return Promise.resolve(new Response(JSON.stringify({ task_id: 'one', status: 'succeeded', result_available: true })))
      signal = init!.signal as AbortSignal
      return new Promise<Response>((resolve) => { finish = resolve })
    }))
    const view = render(<TaskRecoveryNotice backendURL="" />)
    fireEvent.click(await screen.findByRole('button', { name: '下载任务结果 one' }))
    view.rerender(<TaskRecoveryNotice backendURL="https://other" />)
    expect(signal.aborted).toBe(true)
    await act(async () => { finish(new Response('{"image":"stale"}')) })
    expect(downloadRecoveredResult).not.toHaveBeenCalled()
  })

  it('unlocks the download button after timeout without submitting a new task', async () => {
    remember()
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      if (!url.endsWith('/result')) return Promise.resolve(new Response(JSON.stringify({ task_id: 'one', status: 'succeeded', result_available: true })))
      return new Promise<Response>((_resolve, reject) => {
        init!.signal!.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
      })
    }))
    render(<TaskRecoveryNotice backendURL="" />)
    const button = await screen.findByRole('button', { name: '下载任务结果 one' })
    vi.useFakeTimers()
    fireEvent.click(button)
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(screen.getByText(/下载超时/)).toBeInTheDocument()
    expect(button).toBeEnabled()
    expect(downloadRecoveredResult).not.toHaveBeenCalled()
    cleanup()
    expect(vi.getTimerCount()).toBe(0)
  })
})
