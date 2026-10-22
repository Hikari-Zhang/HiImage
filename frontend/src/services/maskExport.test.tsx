import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import { useMaskExport } from '../hooks/useMaskExport'
import { exportMask, exportCombinedMask } from './maskExport'

class FakeWorker {
  static instances: FakeWorker[] = []
  onmessage: ((event: { data: unknown }) => void) | null = null
  onerror: ((event: { message: string }) => void) | null = null
  onmessageerror: (() => void) | null = null
  terminate = vi.fn()
  postMessage = vi.fn()
  constructor() { FakeWorker.instances.push(this) }
  complete(value: string) { this.onmessage?.({ data: { type: 'export-complete', dataURL: value } }) }
}

beforeEach(() => { FakeWorker.instances = []; vi.stubGlobal('Worker', FakeWorker) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('job-owned mask exports', () => {
  it('creates no worker for an idle page and finishes an export after the page unmounts', async () => {
    const hook = renderHook(() => useMaskExport())
    expect(FakeWorker.instances).toHaveLength(0)
    const result = hook.result.current.exportMask([], 8, 8)
    const worker = FakeWorker.instances[0]
    hook.unmount()
    expect(worker.terminate).not.toHaveBeenCalled()
    worker.complete('data:image/png;base64,mask')
    await expect(result).resolves.toBe('data:image/png;base64,mask')
    expect(worker.terminate).toHaveBeenCalledOnce()
    expect(worker.onmessage).toBeNull()
  })

  it('keeps concurrent exports independent and preserves ROI coordinates', async () => {
    const first = exportCombinedMask([], [{ x1: 1, y1: 2, x2: 3, y2: 4 }], 8, 8)
    const second = exportMask([], 16, 16)
    expect(FakeWorker.instances[0].postMessage).toHaveBeenCalledWith(expect.objectContaining({ rois: [[1, 2, 3, 4]] }))
    FakeWorker.instances[1].complete('second')
    await expect(second).resolves.toBe('second')
    expect(FakeWorker.instances[0].terminate).not.toHaveBeenCalled()
    FakeWorker.instances[0].complete('first')
    await expect(first).resolves.toBe('first')
  })

  it.each(['export-error', 'error', 'messageerror'] as const)('rejects %s and releases the worker', async (kind) => {
    const result = exportMask([], 8, 8)
    const rejected = expect(result).rejects.toThrow()
    const worker = FakeWorker.instances[0]
    if (kind === 'export-error') worker.onmessage?.({ data: { type: kind, error: 'canvas failure' } })
    if (kind === 'error') worker.onerror?.({ message: 'worker failure' })
    if (kind === 'messageerror') worker.onmessageerror?.()
    await rejected
    expect(worker.terminate).toHaveBeenCalledOnce()
  })

  it('times out an unresponsive worker instead of leaving the task pending forever', async () => {
    vi.useFakeTimers()
    const result = exportMask([], 8, 8)
    const rejected = expect(result).rejects.toThrow('超时')
    await vi.advanceTimersByTimeAsync(60000)
    await rejected
    expect(FakeWorker.instances[0].terminate).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })
})
