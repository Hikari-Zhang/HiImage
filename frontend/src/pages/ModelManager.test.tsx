import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import ModelManager from './ModelManager'
import { useDownloadStore } from '../stores/useDownloadStore'
import { resetModelListCache } from '../hooks/useModelList'

vi.mock('../hooks/useDownloadManager', () => ({ useDownloadManager: () => ({
  startDownload: vi.fn(), cancelDownload: vi.fn(), startBulkDownload: vi.fn(), cancelBulkDownload: vi.fn(),
}) }))
afterEach(() => {
  cleanup(); resetModelListCache(); vi.unstubAllGlobals()
  useDownloadStore.setState({ tasks: {}, bulkSession: { active: false, modelIds: [] } })
})

describe('model list page lifecycle', () => {
  it('shows every affected model before deleting a shared resource', async () => {
    const originalShowModal = HTMLDialogElement.prototype.showModal
    const originalClose = HTMLDialogElement.prototype.close
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        return new Response(JSON.stringify({ ok: true, message: '已删除', affected_model_ids: ['first', 'second'] }))
      }
      if (url.endsWith('/file-resources')) {
        return new Response(JSON.stringify({ resources: [{
          id: 'hf:owner/shared',
          label: 'owner/shared',
          affected_models: [{ id: 'first', name: '当前模型' }, { id: 'second', name: '共享模型' }],
        }] }))
      }
      return new Response(JSON.stringify({ models: [{
        id: 'first', name: '当前模型', provider: 'diffusers', tags: [], status: 'ok', message: '', has_local_path: true,
      }] }))
    })
    vi.stubGlobal('fetch', fetchMock)
    useDownloadStore.getState().setTask('second', { status: 'done' })
    try {
      render(<ModelManager />)
      fireEvent.click(await screen.findByRole('button', { name: '删除 当前模型 的模型权重' }))
      const dialog = await screen.findByRole('dialog', { name: '删除模型权重' })
      expect(within(dialog).getByText('共享模型')).toBeInTheDocument()
      expect(fetchMock.mock.calls.some(([, request]) => request?.method === 'DELETE')).toBe(false)
      fireEvent.click(within(dialog).getByRole('button', { name: '确认删除' }))
      await waitFor(() => expect(useDownloadStore.getState().getTask('second')).toBeUndefined())
      const request = fetchMock.mock.calls.find(([, item]) => item?.method === 'DELETE')?.[1]
      expect(JSON.parse(String(request?.body))).toEqual({
        resource_id: 'hf:owner/shared', affected_model_ids: ['first', 'second'],
      })
    } finally {
      cleanup()
      HTMLDialogElement.prototype.showModal = originalShowModal
      HTMLDialogElement.prototype.close = originalClose
    }
  })

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
