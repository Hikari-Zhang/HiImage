import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import ModelManager from './ModelManager'
import { useDownloadStore } from '../stores/useDownloadStore'

vi.mock('../hooks/useDownloadManager', () => ({ useDownloadManager: () => ({
  startDownload: vi.fn(), cancelDownload: vi.fn(), startBulkDownload: vi.fn(), cancelBulkDownload: vi.fn(),
}) }))
afterEach(() => {
  cleanup(); vi.unstubAllGlobals()
  useDownloadStore.setState({ tasks: {}, bulkSession: { active: false, modelIds: [] } })
})

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
        id: 'test', name: '测试模型', provider: 'diffusers', tags: [], status: 'missing', message: '',
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

  it('shows unconfirmed connection state without counting it as a failed download', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ models: [{
      id: 'test', name: '待确认模型', provider: 'diffusers', tags: [], status: 'missing', message: '',
    }] }))))
    useDownloadStore.getState().setTask('test', {
      modelName: '待确认模型', status: 'error', statusSyncFailed: true, downloadId: 'unconfirmed-ui',
      message: '无法确认下载状态，请重新连接；不代表下载失败',
    })
    useDownloadStore.getState().setBulkSession({ active: true, modelIds: ['test'] })
    render(<ModelManager />)
    expect(await screen.findByText('下载状态未确认')).toBeInTheDocument()
    expect(screen.queryByText('下载失败')).not.toBeInTheDocument()
    expect(screen.getByTitle('重新连接下载状态')).toBeInTheDocument()
    expect(screen.getByText(/1 个待重新连接/)).toBeInTheDocument()
    expect(screen.queryByText(/1 个失败/)).not.toBeInTheDocument()
  })

  it('still labels an actual backend download error as failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ models: [{
      id: 'test', name: '失败模型', provider: 'diffusers', tags: [], status: 'missing', message: '',
    }] }))))
    useDownloadStore.getState().setTask('test', { status: 'error', message: '服务器下载失败' })
    render(<ModelManager />)
    expect(await screen.findByText('下载失败')).toBeInTheDocument()
    expect(screen.queryByTitle('重新连接下载状态')).not.toBeInTheDocument()
  })
})
