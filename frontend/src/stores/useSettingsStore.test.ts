import { afterEach, describe, expect, it, vi } from 'vitest'
import { useSettingsStore } from './useSettingsStore'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useSettingsStore.loadSettings', () => {
  it('does not hydrate a late response from an aborted page request', async () => {
    let finish!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve })))
    useSettingsStore.setState({ defaultDilation: 20 })
    const controller = new AbortController()
    const loading = useSettingsStore.getState().loadSettings('', controller.signal)
    const rejected = expect(loading).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort()
    finish(new Response(JSON.stringify({ default_dilation: 10 })))
    await rejected
    expect(useSettingsStore.getState().defaultDilation).toBe(20)
    expect(useSettingsStore.getState().isLoading).toBe(false)
  })

  it('throws a friendly error for a failed response', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '服务拒绝读取设置' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    })
    try {
      await expect(useSettingsStore.getState().loadSettings('')).rejects.toThrow('服务拒绝读取设置')
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('hydrates only the configured state and keeps the token input empty', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      hf_endpoint: 'https://hf-mirror.example',
      hf_token_configured: true,
      hf_token: 'hf_must_not_be_hydrated',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })))

    await useSettingsStore.getState().loadSettings('')

    expect(useSettingsStore.getState().hfToken).toBe('')
    expect(useSettingsStore.getState().hfTokenConfigured).toBe(true)
  })
})

describe('useSettingsStore token persistence', () => {
  it('omits an empty token when saving other settings', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ status: 'ok' })))
    vi.stubGlobal('fetch', fetchMock)
    useSettingsStore.setState({
      hfToken: '',
      hfTokenConfigured: true,
      hfEndpoint: 'https://new-endpoint.example',
    })

    await useSettingsStore.getState().saveSettings('')

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body.hf_endpoint).toBe('https://new-endpoint.example')
    expect(body).not.toHaveProperty('hf_token')
    expect(body).not.toHaveProperty('clear_hf_token')
  })

  it('sends an explicit clear request and resets configured state', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ status: 'ok' })))
    vi.stubGlobal('fetch', fetchMock)
    useSettingsStore.setState({ hfToken: '', hfTokenConfigured: true })

    await useSettingsStore.getState().clearHfToken('')

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body).toEqual({ clear_hf_token: true })
    expect(useSettingsStore.getState().hfTokenConfigured).toBe(false)
  })
})
