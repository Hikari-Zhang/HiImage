import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ToastContainer from '../components/ui/Toast'
import { useBackendStore } from '../stores/useBackendStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import Settings from './Settings'

vi.mock('./ModelManager', () => ({ default: () => <div>模型管理内容</div> }))

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

describe('Settings', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    useBackendStore.setState({ backendURL: '', isConnected: false, wsConnected: false })
    useSettingsStore.setState({ device: 'cpu', serverPort: 51821, isLoading: false })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('展示设置加载失败提示并保留健康状态刷新能力', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/settings')) {
        return jsonResponse({ detail: '配置文件损坏' }, 500)
      }
      if (url.endsWith('/api/health')) return jsonResponse({ status: 'ok' })
      if (url.endsWith('/api/devices')) return jsonResponse({ devices: [] })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <>
        <Settings isVisible />
        <ToastContainer />
      </>,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('读取设置失败: 配置文件损坏')
    await waitFor(() => expect(screen.getByText('服务正常')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: '刷新健康状态' }))
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/health'))).toHaveLength(2)
  })

  it('队列满时在页面展示统一的可见错误', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/settings')) {
        return jsonResponse({
          code: 'INFERENCE_QUEUE_FULL',
          message: 'backend message',
          retryable: true,
        }, 429)
      }
      if (url.endsWith('/api/health')) return jsonResponse({ status: 'ok' })
      if (url.endsWith('/api/devices')) return jsonResponse({ devices: [] })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Settings isVisible />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '服务器任务队列已满，请稍后重试',
    )
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/settings'))).toHaveLength(1)
  })

  it('加载后同步持久化 tile，保存其他字段时不会重置为零', async () => {
    const savedBodies: unknown[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/settings') && init?.method === 'PUT') {
        savedBodies.push(JSON.parse(String(init.body)))
        return jsonResponse({ ok: true })
      }
      if (url.endsWith('/api/settings')) {
        return jsonResponse({ device: 'cpu', server_port: 51821, upscale_tile: 512 })
      }
      if (url.endsWith('/api/health')) return jsonResponse({ status: 'ok' })
      if (url.endsWith('/api/devices')) return jsonResponse({ devices: [] })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Settings isVisible />)

    expect(await screen.findByDisplayValue('512')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(savedBodies).toHaveLength(1))
    expect(savedBodies[0]).toMatchObject({ upscale_tile: 512 })
  })

  it('显示 Token 已配置状态且确认后显式清除', async () => {
    const savedBodies: unknown[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/settings') && init?.method === 'PUT') {
        savedBodies.push(JSON.parse(String(init.body)))
        return jsonResponse({ status: 'ok' })
      }
      if (url.endsWith('/api/settings')) {
        return jsonResponse({ hf_token_configured: true })
      }
      if (url.endsWith('/api/health')) return jsonResponse({ status: 'ok' })
      if (url.endsWith('/api/devices')) return jsonResponse({ devices: [] })
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<Settings isVisible />)

    expect(await screen.findByText('Token 已配置')).toBeInTheDocument()
    expect(screen.getByLabelText('HuggingFace Token')).toHaveValue('')
    await userEvent.click(screen.getByRole('button', { name: '清除已配置 Token' }))

    await waitFor(() => expect(savedBodies).toContainEqual({ clear_hf_token: true }))
    expect(window.confirm).toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByText('Token 已配置')).not.toBeInTheDocument())
  })
})
