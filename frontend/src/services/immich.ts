import { create } from 'zustand'
import { createApiClient, ensureApiSuccess } from './apiClient'
import { useBackendStore } from '../stores/useBackendStore'

export type ImmichConnection = { configured: boolean; url: string; generation: string; mode: 'read' | 'upload' | 'replace' }
export type ImmichAsset = { id: string; name: string; taken?: string }
export type ImmichAlbum = { id: string; name: string }
export type ImmichSource = { token: string; name: string; taken?: string; backend: string; revision: number }
export type ImmichEditor = 'watermark' | 'upscale' | 'cutout' | 'prompt-edit'

const ACCESS_SESSION_KEY = 'hiimage:immich-access'

function restoreAccess(): { access: string; accessBackend: string } {
  try {
    const saved = JSON.parse(sessionStorage.getItem(ACCESS_SESSION_KEY) ?? 'null')
    if (typeof saved?.access === 'string' && typeof saved.accessBackend === 'string') {
      return { access: saved.access, accessBackend: saved.accessBackend }
    }
  } catch {
    // Storage may be unavailable in this browser.
  }
  return { access: '', accessBackend: '' }
}

export const useImmich = create<{
  access: string
  accessBackend: string
  sources: Partial<Record<ImmichEditor, ImmichSource>>
}>(() => ({ ...restoreAccess(), sources: {} }))

export function rememberImmichAccess(access: string, accessBackend: string) {
  const saved = { access, accessBackend }
  useImmich.setState(saved)
  try {
    sessionStorage.setItem(ACCESS_SESSION_KEY, JSON.stringify(saved))
  } catch {
    // The current page can still use the token.
  }
}

export function bindImmichSource(editor: ImmichEditor, source: ImmichSource) {
  useImmich.setState(state => ({ sources: { ...state.sources, [editor]: source } }))
}

export function immichApi(enteredToken?: string) {
  const backend = useBackendStore.getState().backendURL
  const { access, accessBackend } = useImmich.getState()
  const client = createApiClient(backend)
  const options = (init?: RequestInit) => {
    const headers = new Headers(init?.headers)
    const token = enteredToken ?? (backend === accessBackend ? access : '')
    if (token) headers.set('X-HiImage-Immich-Access', token)
    return { ...init, headers, redirect: 'error' as const, cache: 'no-store' as const }
  }
  return {
    backend,
    json: <T>(path: string, init?: RequestInit) => client.json<T>('/api/immich' + path, options(init)),
    fetch: async (path: string, init?: RequestInit) => {
      const response = await client.fetch('/api/immich' + path, options(init))
      await ensureApiSuccess(response)
      return response
    },
  }
}

export function jsonRequest(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

export function operationId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
}
