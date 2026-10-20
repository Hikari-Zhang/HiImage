export type ApiClient = {
  baseUrl: string
  url: (path: string) => string
  sseUrl: (path: string) => string
  websocketUrl: (path: string) => string
  fetch: (path: string, init?: RequestInit) => Promise<Response>
}

export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  readonly retryable: boolean

  constructor(message: string, status: number, code?: string, retryable = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.retryable = retryable
  }
}

const QUEUE_FULL_MESSAGE = '服务器任务队列已满，请稍后重试'

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
  const request = async (path: string, init?: RequestInit): Promise<Response> => {
    const response = await fetch(url(path), init)
    if (response.status !== 429) return response

    let body: { code?: string; retryable?: boolean } = {}
    try {
      body = await response.json()
    } catch {
      // 429 即使响应体损坏，也使用统一的可恢复错误。
    }
    throw new ApiError(
      QUEUE_FULL_MESSAGE,
      response.status,
      body.code,
      body.retryable ?? true,
    )
  }
  return { baseUrl: base, url, sseUrl, websocketUrl, fetch: request }
}

export const apiClient = createApiClient()
