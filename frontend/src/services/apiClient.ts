export type ApiClient = {
  baseUrl: string
  url: (path: string) => string
  sseUrl: (path: string) => string
  websocketUrl: (path: string) => string
  fetch: (path: string, init?: RequestInit) => Promise<Response>
}

function trim(value: string): string {
  return value.replace(/\/+$/, '')
}

export function createApiClient(baseUrl?: string): ApiClient {
  const base = trim(baseUrl || import.meta.env.VITE_API_BASE_URL || '')
  const url = (path: string) => `${base}/${path.replace(/^\/+/, '')}`.replace(/^\/(?=\/)/, '')
  const sseUrl = url
  const websocketUrl = (path: string) => {
    const absolute = url(path)
    if (absolute.startsWith('https://')) return `wss://${absolute.slice('https://'.length)}`
    if (absolute.startsWith('http://')) return `ws://${absolute.slice('http://'.length)}`
    if (typeof window !== 'undefined' && window.location) {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      return `${protocol}//${window.location.host}${absolute}`
    }
    return `ws://localhost${absolute}`
  }
  return { baseUrl: base, url, sseUrl, websocketUrl, fetch: (path, init) => fetch(url(path), init) }
}

export const apiClient = createApiClient()
