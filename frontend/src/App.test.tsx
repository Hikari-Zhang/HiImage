import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { useSettingsStore } from './stores/useSettingsStore'
import App from './App'
import { rememberPendingTask } from './services/taskRecoveryStorage'
import { useProcessStore } from './stores/useProcessStore'

vi.mock('./components/layout/MainLayout', () => ({
  default: () => <div>业务页面</div>,
}))

vi.mock('./stores/useModelStore', () => ({
  useModelStore: (selector: (state: { loadModels: () => Promise<void> }) => unknown) =>
    selector({ loadModels: vi.fn(async () => undefined) }),
}))

describe('App settings hydration', () => {
  beforeEach(() => {
    sessionStorage.clear()
    useProcessStore.setState({ tasks: {}, sessions: {} })
    window.location.hash = '#/upscale'
    useSettingsStore.setState({ device: 'cpu', isLoading: false })
  })

  afterEach(() => {
    cleanup()
    sessionStorage.clear()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('hydrates persistent settings before rendering a directly entered processing page', async () => {
    let resolveFetch!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })))

    render(<App />)

    expect(screen.getByText('正在加载应用设置…')).toBeInTheDocument()
    expect(screen.queryByText('业务页面')).not.toBeInTheDocument()

    resolveFetch(new Response(JSON.stringify({ device: 'cuda', upscale_tile: 512 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    expect(await screen.findByText('业务页面')).toBeInTheDocument()
    expect(useSettingsStore.getState().device).toBe('cuda')
    expect(useSettingsStore.getState().upscaleTile).toBe(512)
  })

  it('shows a clear error and allows entry when settings hydration fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: '配置不可用' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))

    render(<App />)

    expect(await screen.findByText('业务页面')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('读取应用设置失败：配置不可用')
  })

  it('queries pre-refresh task metadata while loading the application without posting inference', async () => {
    rememberPendingTask({ taskId: 'before-refresh', backendURL: '', sessionId: 'upscale', createdAt: Date.now() })
    const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(
      url === '/api/tasks/before-refresh'
        ? { task_id: 'before-refresh', status: 'running', progress: 60 }
        : { device: 'cpu' },
    )))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(await screen.findByText(/后台仍在处理（60%）/)).toBeInTheDocument()
    expect(await screen.findByText('业务页面')).toBeInTheDocument()
    expect(useProcessStore.getState().tasks).toEqual({})
    expect(fetch.mock.calls.map(([url]) => url).sort()).toEqual(['/api/settings', '/api/tasks/before-refresh'])
  })
})
