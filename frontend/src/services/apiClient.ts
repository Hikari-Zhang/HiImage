export type ApiClient = {
  baseUrl: string
  url: (path: string) => string
  sseUrl: (path: string) => string
  websocketUrl: (path: string) => string
  fetch: (path: string, init?: RequestInit) => Promise<Response>
  json: <T>(path: string, init?: RequestInit, failureMessage?: string) => Promise<T>
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

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined
}

function errorDetail(value: unknown): string | undefined {
  if (typeof value === 'string') return value.trim() || undefined
  if (Array.isArray(value)) return value.map(errorDetail).filter(Boolean).join('；') || undefined
  const record = object(value)
  if (!record) return undefined
  // FastAPI validation errors contain an array of {loc, msg, type} entries.
  if (typeof record.msg === 'string') {
    const location = Array.isArray(record.loc) ? record.loc.filter((part) => part !== 'body').join('.') : ''
    return location ? `${location}: ${record.msg}` : record.msg
  }
  return errorDetail(record.message) || errorDetail(record.detail)
}

async function readBody(response: Response) {
  const text = await response.text()
  try {
    return { text, data: JSON.parse(text) as unknown, valid: true }
  } catch {
    return { text, data: undefined, valid: false }
  }
}

function responseError(response: Response, body: Awaited<ReturnType<typeof readBody>>, fallback: string): ApiError {
  const record = object(body.data)
  // Proxy HTML pages should not become a wall of markup in a toast.
  const plain = !body.valid && body.text.trim() && !body.text.trim().startsWith('<')
    ? body.text.trim().slice(0, 500) : undefined
  return new ApiError(
    response.status === 429 ? QUEUE_FULL_MESSAGE
      : errorDetail(body.data) || plain || `${fallback} (${response.status})`,
    response.status,
    typeof record?.code === 'string' ? record.code : undefined,
    typeof record?.retryable === 'boolean' ? record.retryable : response.status === 429,
  )
}

export async function ensureApiSuccess(response: Response, failureMessage = '请求失败'): Promise<void> {
  if (!response.ok) throw responseError(response, await readBody(response), failureMessage)
}

export async function readApiResponse<T>(response: Response, failureMessage = '请求失败'): Promise<T> {
  const body = await readBody(response)
  if (!response.ok) throw responseError(response, body, failureMessage)
  if (response.status === 204) return undefined as T
  if (!body.valid) throw new ApiError(`${failureMessage}：服务返回了无效的 JSON 响应`, response.status, 'INVALID_RESPONSE')
  return body.data as T
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
  const request = async (path: string, init?: RequestInit): Promise<Response> => {
    const response = await fetch(url(path), init)
    if (response.status !== 429) return response

    let body: Awaited<ReturnType<typeof readBody>>
    try {
      body = await readBody(response)
    } catch {
      body = { text: '', data: undefined, valid: false }
    }
    throw responseError(response, body, QUEUE_FULL_MESSAGE)
  }
  const json: ApiClient['json'] = async <T>(path: string, init?: RequestInit, failureMessage?: string) =>
    readApiResponse<T>(await request(path, init), failureMessage)
  return { baseUrl: base, url, sseUrl, websocketUrl, fetch: request, json }
}

export const apiClient = createApiClient()
