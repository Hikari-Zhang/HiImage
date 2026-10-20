import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { useSettingsStore } from './stores/useSettingsStore'
import App from './App'

vi.mock('./components/layout/MainLayout', () => ({
  default: () => <div>业务页面</div>,
}))

vi.mock('./stores/useModelStore', () => ({
  useModelStore: (selector: (state: { loadModels: () => Promise<void> }) => unknown) =>
    selector({ loadModels: vi.fn(async () => undefined) }),
}))

describe('App settings hydration', () => {
  beforeEach(() => {
    window.location.hash = '#/upscale'
    useSettingsStore.setState({ device: 'cpu', isLoading: false })
  })

  afterEach(() => {
    cleanup()
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
})
