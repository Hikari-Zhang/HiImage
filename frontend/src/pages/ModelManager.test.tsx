import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import ModelManager from './ModelManager'

vi.mock('../hooks/useDownloadManager', () => ({ useDownloadManager: () => ({
  startDownload: vi.fn(), cancelDownload: vi.fn(), startBulkDownload: vi.fn(), cancelBulkDownload: vi.fn(),
}) }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('model list page lifecycle', () => {
  it('cancels abandoned requests under StrictMode and still loads the current request', async () => {
    const signals: AbortSignal[] = []
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      const signal = init!.signal as AbortSignal
      signals.push(signal)
      if (signals.length === 1) return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
      })
      return Promise.resolve(new Response(JSON.stringify({ models: [{
        id: 'test', name: '测试模型', provider: 'iopaint', tags: [], status: 'missing', message: '',
      }] })))
    }))
    const page = render(<StrictMode><ModelManager /></StrictMode>)
    expect(await screen.findByText('测试模型')).toBeInTheDocument()
    expect(signals).toHaveLength(2)
    expect(signals[0].aborted).toBe(true)
    expect(signals[1].aborted).toBe(false)
    page.unmount()
  })

  it('cancels an unfinished list request on unmount', () => {
    let signal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init!.signal as AbortSignal
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
      })
    }))
    const page = render(<ModelManager />)
    expect(signal.aborted).toBe(false)
    page.unmount()
    expect(signal.aborted).toBe(true)
  })
})
